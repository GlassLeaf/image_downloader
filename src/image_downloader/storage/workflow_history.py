"""Profile-wide, atomically merged workflow summaries and retention."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from ..configuration.models import WorkflowHistory as HistoryPolicy
from ..exceptions import UpdateStateError
from ..immutable import thaw_json
from ..models import WorkflowPruneResult, WorkflowRunRecord
from .filesystem import FileSystem
from .interprocess_lock import InterProcessFileLock


def run_payload(record: WorkflowRunRecord) -> dict[str, object]:
    return {
        "run_id": record.run_id,
        "plugin_id": record.plugin_id,
        "feed_key": record.feed_key,
        "source_url": record.source_url,
        "started_at": record.started_at.isoformat(),
        "ended_at": record.ended_at.isoformat(),
        "exit_code": record.exit_code,
        "details": thaw_json(record.details),
    }


def encode_records(records: list[WorkflowRunRecord]) -> bytes:
    return json.dumps(
        {"schema_version": 1, "runs": [run_payload(r) for r in records]},
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def parse_time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("invalid timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timestamp needs a timezone")
    return parsed.astimezone(UTC)


def _record(value: Any) -> WorkflowRunRecord:
    if not isinstance(value, dict) or set(value) != {
        "run_id",
        "plugin_id",
        "feed_key",
        "source_url",
        "started_at",
        "ended_at",
        "exit_code",
        "details",
    }:
        raise ValueError("invalid run")
    if not isinstance(value["run_id"], str) or str(UUID(value["run_id"])) != value["run_id"]:
        raise ValueError("invalid run ID")
    if value["plugin_id"] is not None and (not isinstance(value["plugin_id"], str) or not value["plugin_id"]):
        raise ValueError("invalid plugin ID")
    key = value["feed_key"]
    if not isinstance(key, str) or len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
        raise ValueError("invalid feed key")
    if not isinstance(value["source_url"], str) or not value["source_url"]:
        raise ValueError("invalid source URL")
    if type(value["exit_code"]) is not int or value["exit_code"] not in {0, 1, 2, 3, 4, 5, 130}:
        raise ValueError("invalid exit code")
    details = _details(value["details"])
    if parse_time(value["ended_at"]) < parse_time(value["started_at"]):
        raise ValueError("end precedes start")
    return WorkflowRunRecord(
        value["run_id"],
        value["plugin_id"],
        key,
        value["source_url"],
        parse_time(value["started_at"]),
        parse_time(value["ended_at"]),
        value["exit_code"],
        details,
    )


def _details(details: Any) -> dict[str, Any]:
    from .workflow_history_schema import HistoryDetails

    return HistoryDetails.model_validate(details).model_dump()


def _cutoff(now: datetime, days: int) -> datetime:
    try:
        return now - timedelta(days=days)
    except OverflowError:
        return datetime.min.replace(tzinfo=UTC)


class WorkflowHistoryStore:
    def __init__(self, filesystem: FileSystem, policy: HistoryPolicy, *, lock_timeout_seconds: float = 30.0) -> None:
        self.filesystem, self.policy = filesystem, policy
        self.lock_timeout_seconds = lock_timeout_seconds
        self.relative = Path("workflow-history.json")

    def lock(self) -> InterProcessFileLock:
        self.filesystem.ensure_directory()
        relative = Path("workflow-history.lock")
        self.filesystem.exists(relative)
        return InterProcessFileLock(self.filesystem.path(relative), timeout_seconds=self.lock_timeout_seconds)

    def _read(self) -> tuple[list[WorkflowRunRecord], int]:
        if not self.filesystem.exists(self.relative):
            return [], 0
        try:
            text = self.filesystem.read_text(self.relative)
            value = json.loads(text, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid number")))
            if not isinstance(value, dict) or set(value) != {"schema_version", "runs"}:
                raise ValueError("invalid document")
            if type(value["schema_version"]) is not int or value["schema_version"] != 1:
                raise ValueError("unsupported schema")
            if not isinstance(value["runs"], list):
                raise ValueError("invalid runs")
            records = [_record(r) for r in value["runs"]]
            if len({r.run_id for r in records}) != len(records):
                raise ValueError("duplicate run IDs")
            records.sort(key=lambda r: (r.ended_at, r.run_id))
            # read_text normalizes CRLF; retention measures the actual stored UTF-8 bytes.
            return records, self.filesystem.path(self.relative).stat().st_size
        except (OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
            raise UpdateStateError("workflow history has an invalid schema or cannot be read") from exc

    def visible(self, now: datetime | None = None) -> list[WorkflowRunRecord]:
        with self.lock():
            records, _ = self._read()
        cutoff = _cutoff(now or datetime.now(UTC), self.policy.max_age_days)
        return [r for r in records if r.ended_at > cutoff]

    def _retain(
        self, records: list[WorkflowRunRecord], now: datetime, *, incoming: WorkflowRunRecord | None = None
    ) -> tuple[list[WorkflowRunRecord], int]:
        cutoff = _cutoff(now, self.policy.max_age_days)
        retained = [r for r in records if r.ended_at > cutoff]
        expired = len(records) - len(retained)
        oversized = incoming is not None and len(encode_records([incoming])) > self.policy.max_size_bytes
        if not oversized:
            # Serialize each summary once instead of repeatedly encoding the whole journal.
            envelope_size = len(encode_records([]))
            sizes = [len(encode_records([r])) - envelope_size for r in retained]
            total = envelope_size + sum(sizes) + max(0, len(retained) - 1)
            first = 0
            while len(retained) - first > 1 and total > self.policy.max_size_bytes:
                total -= sizes[first] + 1
                first += 1
            retained = retained[first:]
        return retained, expired

    def _write(self, records: list[WorkflowRunRecord]) -> None:
        try:
            self.filesystem.write_bytes_atomic(self.relative, encode_records(records))
        except OSError as exc:
            raise UpdateStateError("workflow history cannot be saved") from exc

    def save(self, record: WorkflowRunRecord, *, now: datetime | None = None) -> bool:
        with self.lock():
            records, _ = self._read()
            records = [r for r in records if r.run_id != record.run_id]
            records.append(record)
            records.sort(key=lambda r: (r.ended_at, r.run_id))
            records, _ = self._retain(records, now or datetime.now(UTC), incoming=record)
            self._write(records)
            return len(encode_records(records)) > self.policy.max_size_bytes

    def revise(self, run_id: str, details: dict[str, object], exit_code: int) -> bool:
        with self.lock():
            records, _ = self._read()
            old = next((r for r in records if r.run_id == run_id), None)
            if old is None:
                raise UpdateStateError("workflow run is no longer stored")
            record = replace(old, details=details, exit_code=exit_code, ended_at=max(old.started_at, datetime.now(UTC)))
            records = [r for r in records if r.run_id != run_id] + [record]
            records.sort(key=lambda r: (r.ended_at, r.run_id))
            records, _ = self._retain(records, datetime.now(UTC), incoming=record)
            self._write(records)
            return len(encode_records(records)) > self.policy.max_size_bytes

    def prune(self, *, dry_run: bool = False, now: datetime | None = None) -> WorkflowPruneResult:
        with self.lock():
            records, before = self._read()
            retained, expired = self._retain(records, now or datetime.now(UTC))
            ids = {r.run_id for r in retained}
            deleted = tuple(r.run_id for r in records if r.run_id not in ids)
            after = len(encode_records(retained)) if before else 0
            if not dry_run and before:
                self._write(retained)
            return WorkflowPruneResult(deleted, before, after, expired, after > self.policy.max_size_bytes, dry_run)
