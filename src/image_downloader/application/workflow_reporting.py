"""Shared exit status and redacted, URL-level workflow history projection."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import cast

from ..exceptions import AuthenticationError, ConfigurationError, ErrorInfo, PluginError
from ..models import DownloadResult, WorkflowResult
from ..privacy.log_safety import safe_exception_name, safe_relative_path, safe_url
from .additional_files import additional_file_payload


def stopped_status(error: BaseException) -> int:
    if isinstance(error, asyncio.CancelledError):
        return 130
    if isinstance(error, ConfigurationError):
        return 2
    if isinstance(error, AuthenticationError):
        return 3
    return 4 if isinstance(error, PluginError) else 1


def workflow_status(result: WorkflowResult) -> int:
    if not result.timed_out and all(i.status in {"success", "removed"} for i in result.items):
        return 0
    saved = any(
        (i.download is not None and (i.download.saved_files or i.download.skipped_files))
        or any(a.download is not None and (a.download.saved_files or a.download.skipped_files) for a in i.attempts)
        for i in result.items
    )
    return 5 if saved else 1


def outcome(*, cancelled: bool, timed_out: bool, stopped: bool, incomplete: bool) -> str:
    """Describe the whole execution independently of URL-level counters."""
    if cancelled:
        return "cancelled"
    if timed_out:
        return "timed_out"
    if stopped:
        return "stopped"
    return "incomplete" if incomplete else "success"


def workflow_outcome(result: WorkflowResult) -> str:
    return outcome(
        cancelled=result.cancelled,
        timed_out=result.timed_out,
        stopped=result.stop_error is not None,
        incomplete=any(i.status not in {"success", "removed"} for i in result.items),
    )


def history_outcome(details: Mapping[str, object]) -> str:
    summary = cast(Mapping[str, int], details["summary"])
    return outcome(
        cancelled=bool(details["cancelled"]),
        timed_out=bool(details["timed_out"]),
        stopped=details["stop_error"] is not None,
        incomplete=any(summary[s] for s in ("partial", "failed", "unprocessed")),
    )


def safe_error(error: ErrorInfo | None, root: Path) -> dict[str, object] | None:
    if error is None:
        return None
    data = asdict(error)
    data["exception"] = safe_exception_name(error.exception)
    if error.response_url:
        data["response_url"] = safe_url(error.response_url)
    if error.output_path:
        data["output_path"] = safe_relative_path(error.output_path, root)
    return data


def counts(download: DownloadResult | None) -> dict[str, int]:
    return {
        "saved": len(download.saved_files) if download else 0,
        "skipped": len(download.skipped_files) if download else 0,
        "failures": len(download.failures) if download else 0,
    }


def history_details(result: WorkflowResult, root: Path) -> dict[str, object]:
    return {
        "download_scope": result.download_scope,
        "workflow_retries": result.workflow_retries,
        "workflow_retry_delay": result.workflow_retry_delay,
        "workflow_retry_timeout": result.workflow_retry_timeout,
        "timed_out": result.timed_out,
        "cancelled": result.cancelled,
        "stop_error": safe_error(result.stop_error, root),
        "summary": {
            s: sum(i.status == s for i in result.items)
            for s in ("success", "partial", "failed", "unprocessed", "removed")
        },
        "items": [
            {
                "url": safe_url(i.url),
                "reasons": list(i.reasons),
                "status": i.status,
                **counts(i.download),
                "error": safe_error(i.error, root),
                "failure_groups": failure_groups(i.download, root),
                "additional_files": additional_file_payload(i.download, root),
                "attempts": [
                    {
                        "round_number": a.round_number,
                        "status": a.status,
                        **counts(a.download),
                        "error": safe_error(a.error, root),
                        "failure_groups": failure_groups(a.download, root),
                        "additional_files": additional_file_payload(a.download, root),
                    }
                    for a in i.attempts
                ],
            }
            for i in result.items
        ],
        "rounds": [
            {
                "round_number": r.round_number,
                "status": r.status,
                "checked_at": r.snapshot.checked_at.isoformat() if r.snapshot else None,
                "candidate_count": len(r.snapshot.candidates) if r.snapshot else 0,
                "selected_urls": [safe_url(u) for u in r.selected_urls],
                "removed_urls": [safe_url(u) for u in r.removed_urls],
                "changes": [dict(asdict(c), url=safe_url(c.url)) for c in r.changes],
                "attempt_summary": {
                    s: sum(a.status == s for i in result.items for a in i.attempts if a.round_number == r.round_number)
                    for s in ("success", "partial", "failed", "unprocessed")
                },
                "error": safe_error(r.error, root),
            }
            for r in result.rounds
        ],
    }


def failure_groups(download: DownloadResult | None, root: Path) -> list[dict[str, object]]:
    """Aggregate equal safe error contexts without storing per-image outcomes."""
    groups: dict[tuple[object, ...], dict[str, object]] = {}
    for f in download.failures if download else ():
        values = (
            f.kind.value,
            f.code,
            f.http_status,
            safe_url(f.response_url) if f.response_url else None,
            safe_relative_path(f.output_path, root) if f.output_path else None,
        )
        if values not in groups:
            groups[values] = {
                "kind": values[0],
                "code": f.code,
                "http_status": f.http_status,
                "response_url": values[3],
                "output_path": values[4],
                "count": 0,
            }
        groups[values]["count"] = cast(int, groups[values]["count"]) + 1
    return list(groups.values())
