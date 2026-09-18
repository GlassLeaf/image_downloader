"""Versioned, source-scoped persistence for per-profile update snapshots."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from ..exceptions import DownloaderError, PluginError
from ..models import UpdateCandidate, UpdateChange, UpdateChangeKind, UpdateSnapshot
from .filesystem import FileSystem
from .interprocess_lock import InterProcessFileLock

_SCHEMA_VERSION = 2
_Record = dict[str, str | None]


@dataclass(slots=True)
class _SourceSnapshot:
    checked_at: str
    records: dict[str, _Record]


@dataclass(slots=True)
class _StateDocument:
    sources: dict[str, dict[str, _SourceSnapshot]] = field(default_factory=dict)
    legacy_records: dict[str, _Record] = field(default_factory=dict)


class UpdateState:
    def __init__(self, filesystem: FileSystem, *, lock_timeout_seconds: float = 30.0) -> None:
        self.filesystem = filesystem
        self.relative = Path("updates.json")
        self.lock_relative = Path("updates.lock")
        self.lock_timeout_seconds = lock_timeout_seconds

    def records(self) -> dict[str, _Record]:
        """Return legacy unscoped records for callers of the pre-v2 API."""
        return self._read().legacy_records

    def save(self, records: Mapping[str, Mapping[str, str | None]]) -> None:
        """Preserve the pre-v2 save API without discarding scoped snapshots."""
        with self._lock():
            document = self._read()
            document.legacy_records = self._parse_records(dict(records))
            self._write(document)

    def apply_snapshot(
        self,
        plugin_id: str,
        source_url: str,
        snapshot: UpdateSnapshot,
    ) -> tuple[UpdateChange, ...]:
        """Compare and atomically persist one requested feed without touching others."""
        with self._lock():
            return self._apply_snapshot_unlocked(plugin_id, source_url, snapshot)

    async def apply_snapshot_async(
        self,
        plugin_id: str,
        source_url: str,
        snapshot: UpdateSnapshot,
    ) -> tuple[UpdateChange, ...]:
        """Wait for the disk transaction off-loop, finishing it before cancellation exits."""
        task = asyncio.create_task(asyncio.to_thread(self.apply_snapshot, plugin_id, source_url, snapshot))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
            task.result()
            raise

    def _apply_snapshot_unlocked(
        self,
        plugin_id: str,
        source_url: str,
        snapshot: UpdateSnapshot,
    ) -> tuple[UpdateChange, ...]:
        document = self._read()
        plugin_sources = document.sources.setdefault(plugin_id, {})
        source_key = self._source_key(source_url)
        source_exists = source_key in plugin_sources
        previous = plugin_sources[source_key].records if source_exists else {}
        current: dict[str, _Record] = {}
        changes: list[UpdateChange] = []
        for candidate in snapshot.candidates:
            identity = self._identity(candidate)
            if identity in current:
                raise PluginError("check_updates returned duplicate candidate identities")
            old = previous.get(identity)
            if old is None and not source_exists:
                old = self._legacy_match(document.legacy_records, plugin_id, candidate)
            if old is None:
                changes.append(
                    UpdateChange(UpdateChangeKind.ADDED, candidate.url, candidate.content_id, candidate.revision)
                )
            elif old["url"] != candidate.url or old["revision"] != candidate.revision:
                changes.append(
                    UpdateChange(UpdateChangeKind.CHANGED, candidate.url, candidate.content_id, candidate.revision)
                )
            current[identity] = {
                "url": candidate.url,
                "revision": candidate.revision,
                "content_id": candidate.content_id,
            }
        for identity, old in previous.items():
            if identity not in current:
                changes.append(
                    UpdateChange(
                        UpdateChangeKind.REMOVED,
                        old["url"] or "",
                        old["content_id"],
                        old["revision"],
                    )
                )
        plugin_sources[source_key] = _SourceSnapshot(snapshot.checked_at.isoformat(), current)
        self._write(document)
        return tuple(changes)

    def _lock(self) -> InterProcessFileLock:
        self.filesystem.ensure_directory()
        self.filesystem.exists(self.lock_relative)  # Reject an existing symlink/reparse point.
        return InterProcessFileLock(
            self.filesystem.path(self.lock_relative), timeout_seconds=self.lock_timeout_seconds
        )

    @staticmethod
    def _source_key(source_url: str) -> str:
        return sha256(source_url.encode("utf-8")).hexdigest()

    @staticmethod
    def _identity(candidate: UpdateCandidate) -> str:
        if candidate.content_id:
            return f"id:{candidate.content_id}"
        return f"url:{candidate.url}"

    @staticmethod
    def _legacy_match(records: Mapping[str, _Record], plugin_id: str, candidate: UpdateCandidate) -> _Record | None:
        legacy_key = f"{plugin_id}:{candidate.content_id or candidate.url}"
        old = records.get(legacy_key)
        if old is not None and old["url"] == candidate.url and old["content_id"] == candidate.content_id:
            return old
        return None

    def _read(self) -> _StateDocument:
        if not self.filesystem.exists(self.relative):
            return _StateDocument()
        try:
            payload = json.loads(self.filesystem.read_text(self.relative))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DownloaderError("update state cannot be read") from exc
        if not isinstance(payload, dict):
            raise DownloaderError("update state has an invalid schema")
        version = payload.get("schema_version", 1)
        if type(version) is not int or version not in {1, _SCHEMA_VERSION}:
            raise DownloaderError("update state schema version is unsupported")
        if version == 1:
            return _StateDocument(legacy_records=self._parse_records(payload.get("records")))
        sources = payload.get("sources")
        if not isinstance(sources, dict):
            raise DownloaderError("update state has an invalid schema")
        legacy = self._parse_records(payload.get("legacy_records", {}))
        parsed: dict[str, dict[str, _SourceSnapshot]] = {}
        for plugin_id, source_values in sources.items():
            if not isinstance(plugin_id, str) or not isinstance(source_values, dict):
                raise DownloaderError("update state has an invalid source")
            parsed_sources: dict[str, _SourceSnapshot] = {}
            for source_url, source_value in source_values.items():
                if not isinstance(source_url, str) or not isinstance(source_value, dict):
                    raise DownloaderError("update state has an invalid source")
                checked_at = source_value.get("checked_at")
                if not isinstance(checked_at, str):
                    raise DownloaderError("update state has an invalid source")
                parsed_sources[source_url] = _SourceSnapshot(
                    checked_at,
                    self._parse_records(source_value.get("records")),
                )
            parsed[plugin_id] = parsed_sources
        return _StateDocument(parsed, legacy)

    @staticmethod
    def _parse_records(raw: Any) -> dict[str, _Record]:
        if not isinstance(raw, dict):
            raise DownloaderError("update state has an invalid schema")
        records: dict[str, _Record] = {}
        for key, value in raw.items():
            if not isinstance(key, str) or not isinstance(value, dict):
                raise DownloaderError("update state has an invalid record")
            fields = {name: value.get(name) for name in ("url", "revision", "content_id")}
            if any(item is not None and not isinstance(item, str) for item in fields.values()):
                raise DownloaderError("update state has an invalid record")
            records[key] = fields
        return records

    def _write(self, document: _StateDocument) -> None:
        sources = {
            plugin_id: {
                source_url: {"checked_at": source.checked_at, "records": source.records}
                for source_url, source in plugin_sources.items()
            }
            for plugin_id, plugin_sources in document.sources.items()
        }
        self.filesystem.write_bytes_atomic(
            self.relative,
            json.dumps(
                {
                    "schema_version": _SCHEMA_VERSION,
                    "checked_at": datetime.now(UTC).isoformat(),
                    "sources": sources,
                    "legacy_records": document.legacy_records,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8"),
        )
