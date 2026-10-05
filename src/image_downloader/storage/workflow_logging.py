"""Create-only workflow plans and independent, invocation-local progress sinks.

Unlike ordinary diagnostics these files intentionally contain original URLs.
Only the coordinator supplies events: headers, image data and exception text
must never be serialized here.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

from ..exceptions import ErrorInfo, WorkflowPlanLogError, error_reason_for_code
from ..models import WorkflowItemResult, WorkflowLogResult, WorkflowResult
from ..privacy.log_safety import safe_exception_name
from .filesystem import FileSystem


def _json(value: object) -> str:
    # JSON escapes ASCII CR/LF already. Also escape Unicode line separators so
    # text viewers and splitlines() cannot interpret URL data as new records.
    return (
        json.dumps(value, ensure_ascii=False, allow_nan=False)
        .replace("\u0085", "\\u0085")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def safe_error(error: ErrorInfo | None) -> dict[str, object] | None:
    if error is None:
        return None
    # URLs and output paths in errors remain private; only explicit source and
    # target URL fields in the dedicated records are raw.
    return {"code": error.code, "reason": error.reason, "exception": error.exception, "http_status": error.http_status}


def item_summary(item: WorkflowItemResult) -> dict[str, object]:
    download = item.download
    return {
        "url": item.url,
        "reasons": item.reasons,
        "status": item.status,
        "saved": len(download.saved_files) if download else 0,
        "skipped": len(download.skipped_files) if download else 0,
        "failed": len(download.failures) if download else 0,
        "error": safe_error(item.error),
        "image_errors": [
            {
                "code": failure.code if error_reason_for_code(failure.code) else "image_failure",
                "reason": error_reason_for_code(failure.code) or "image failure",
                "exception": safe_exception_name(failure.exception_type),
                "http_status": failure.http_status,
            }
            for failure in download.failures
        ]
        if download
        else [],
        "attempt_count": len(item.attempts),
        "last_attempt_round": item.attempts[-1].round_number if item.attempts else None,
    }


class WorkflowLogWriter:
    def __init__(self, root: Path, run_id: str, started_at: datetime, enabled: bool) -> None:
        self.filesystem = FileSystem(root)
        self.stem = started_at.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "_" + run_id
        self.run_id, self.started_at = run_id, started_at
        self.result = WorkflowLogResult(progress_enabled=enabled)

    def start(self, payload: dict[str, object]) -> None:
        if not self.result.progress_enabled:
            return
        for kind, suffix in (("jsonl", ".jsonl"), ("text", ".log")):
            path = str(self.filesystem.path(self.stem + suffix))
            self.result = (
                replace(self.result, jsonl_path=path) if kind == "jsonl" else replace(self.result, text_path=path)
            )
            try:
                self.filesystem.write_bytes_atomic_new(self.stem + suffix, b"")
                self.result = (
                    replace(self.result, jsonl_saved=True) if kind == "jsonl" else replace(self.result, text_saved=True)
                )
            except Exception:
                self._failed(kind)
        self.event("run_started", payload)

    def _failed(self, kind: str) -> None:
        warning = f"workflow {kind} progress log could not be written; downloads continue"
        self.result = (
            replace(self.result, jsonl_saved=False) if kind == "jsonl" else replace(self.result, text_saved=False)
        )
        self.result = replace(self.result, warnings=(*self.result.warnings, warning))
        try:
            print(warning, file=sys.stderr)
        except Exception:
            pass

    def event(self, name: str, payload: dict[str, object]) -> None:
        if not self.result.progress_enabled:
            return
        previous_warnings = self.result.warnings
        sequence = self.result.event_sequence + 1
        self.result = replace(self.result, event_sequence=sequence)
        record = {
            "format_version": 1,
            "run_id": self.run_id,
            "event_number": sequence,
            "timestamp": datetime.now(UTC).isoformat(),
            "event": name,
            **payload,
        }
        for kind, suffix in (("jsonl", ".jsonl"), ("text", ".log")):
            if not getattr(self.result, f"{kind}_saved"):
                continue
            try:
                # JSON quoting also escapes CR/LF in URLs for the human log.
                body = _json(record)
                if kind == "text":
                    details = " ".join(f"{key}={_json(value)}" for key, value in payload.items())
                    body = f"{record['timestamp']} #{sequence} {name} {details}"
                with self.filesystem.open_text_append(self.stem + suffix) as stream:
                    stream.write(body + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
            except Exception:
                self._failed(kind)
        if self.result.warnings != previous_warnings:
            # A failure in the final text append must still be visible in the
            # surviving JSONL sink (and vice versa), rather than only in the API.
            self.event(
                "recording_failed",
                {
                    "warnings": self.result.warnings,
                    "jsonl_saved": self.result.jsonl_saved,
                    "text_saved": self.result.text_saved,
                },
            )

    def plan(self, number: int, payload: dict[str, object]) -> None:
        document = {
            "format_version": 1,
            "run_id": self.run_id,
            "started_at": self.started_at.isoformat(),
            "planned_at": datetime.now(UTC).isoformat(),
            "round_number": number,
            "run_number": number + 1,
            **payload,
        }
        try:
            path = self.filesystem.write_bytes_atomic_new(
                f"{self.stem}_run{number + 1}.plan.json",
                json.dumps(document, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8"),
            )
        except Exception as exc:
            raise WorkflowPlanLogError() from exc
        self.result = replace(self.result, plan_files=(*self.result.plan_files, str(path)))
        self.event("plan_saved", {"plan_file": str(path), **document})

    def finish(self, result: WorkflowResult, exit_code: int, outcome: str, *, correction: bool = False) -> None:
        self.event(
            "run_corrected" if correction else "run_finished",
            {
                "outcome": outcome,
                "exit_code": exit_code,
                "timed_out": result.timed_out,
                "cancelled": result.cancelled,
                "stop_error": safe_error(result.stop_error),
                "items": [item_summary(item) for item in result.items],
                "recording": asdict(self.result),
            },
        )

    @classmethod
    def resume(cls, result: WorkflowResult) -> WorkflowLogWriter | None:
        summary = result.workflow_log
        if summary is None or not summary.progress_enabled or result.run_id is None:
            return None
        path = summary.jsonl_path or summary.text_path
        if path is None:
            return None
        file = Path(path)
        writer = cls(file.parent, result.run_id, datetime.now(UTC), True)
        writer.stem = file.stem
        writer.result = summary
        return writer
