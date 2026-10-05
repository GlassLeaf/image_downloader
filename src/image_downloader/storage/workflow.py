"""Latest workflow snapshots and durable per-candidate completion state."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import Any, TypeVar

from ..exceptions import PluginError, UpdateStateError
from ..models import UpdateChange, UpdateChangeKind, UpdateSnapshot
from .filesystem import FileSystem
from .interprocess_lock import InterProcessFileLock
from .state import UpdateState

_T = TypeVar("_T")


async def finish_transaction(function: Callable[[], _T]) -> _T:
    task = asyncio.create_task(asyncio.to_thread(function))
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


class WorkflowState:
    def __init__(self, filesystem: FileSystem, *, lock_timeout_seconds: float = 30.0) -> None:
        self.filesystem = filesystem
        self.lock_timeout_seconds = lock_timeout_seconds
        self.relative = Path("workflow.json")

    def lock(self, relative: Path) -> InterProcessFileLock:
        self.filesystem.ensure_directory(relative.parent)
        self.filesystem.exists(relative)
        return InterProcessFileLock(self.filesystem.path(relative), timeout_seconds=self.lock_timeout_seconds)

    def feed_lock(self, plugin_id: str, url: str) -> InterProcessFileLock:
        key = sha256(json.dumps([plugin_id, url]).encode()).hexdigest()
        return self.lock(Path("workflow-locks") / f"{key}.lock")

    def _read(self) -> dict[str, Any]:
        if not self.filesystem.exists(self.relative):
            return {"schema_version": 1, "sources": {}}
        try:
            value = json.loads(self.filesystem.read_text(self.relative))
            self._validate(value)
        except (OSError, UnicodeDecodeError, ValueError, TypeError, KeyError) as exc:
            raise UpdateStateError("workflow state has an invalid schema or cannot be read") from exc
        return value  # type: ignore[no-any-return]

    @staticmethod
    def _validate(value: Any) -> None:
        if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
            raise ValueError("unsupported workflow state")
        sources = value.get("sources")
        if not isinstance(sources, dict):
            raise ValueError("invalid sources")
        for plugin_id, feeds in sources.items():
            if not isinstance(plugin_id, str) or not isinstance(feeds, dict):
                raise ValueError("invalid plugin")
            for key, feed in feeds.items():
                if (
                    not isinstance(key, str)
                    or not isinstance(feed, dict)
                    or not isinstance(feed.get("checked_at"), str)
                ):
                    raise ValueError("invalid feed")
                if not isinstance(feed.get("records"), dict):
                    raise ValueError("invalid records")
                for identity, record in feed["records"].items():
                    WorkflowState._validate_record(identity, record)

    @staticmethod
    def _validate_record(identity: Any, record: Any) -> None:
        if not isinstance(identity, str) or not isinstance(record, dict):
            raise ValueError("invalid record")
        if not isinstance(record.get("url"), str) or not record["url"] or type(record.get("completed")) is not bool:
            raise ValueError("invalid record fields")
        for field in ("content_id", "revision"):
            if field not in record or record[field] is not None and not isinstance(record[field], str):
                raise ValueError("invalid candidate")

    def prepare(
        self, plugin_id: str, url: str, snapshot: UpdateSnapshot, scope: str
    ) -> tuple[tuple[UpdateChange, ...], dict[str, tuple[str, ...]]]:
        _, changes, selected = self.prepare_with_history(plugin_id, url, snapshot, scope)
        return changes, selected

    def prepare_with_history(
        self, plugin_id: str, url: str, snapshot: UpdateSnapshot, scope: str
    ) -> tuple[bool, tuple[UpdateChange, ...], dict[str, tuple[str, ...]]]:
        with self.lock(Path("workflow.lock")):
            document = self._read()
            feeds = document["sources"].setdefault(plugin_id, {})
            source_key = UpdateState._source_key(url)
            history_absent = source_key not in feeds
            previous = feeds.get(source_key, {}).get("records", {})
            current, changes, selected = select_candidates(previous, snapshot, scope)
            feeds[source_key] = {"checked_at": snapshot.checked_at.isoformat(), "records": current}
            self._write(document)
            return history_absent, tuple(changes), selected

    def preview(
        self, plugin_id: str, url: str, snapshot: UpdateSnapshot, scope: str
    ) -> tuple[bool, tuple[UpdateChange, ...], dict[str, tuple[str, ...]]]:
        with self.lock(Path("workflow.lock")):
            document = self._read()
            feeds = document["sources"].get(plugin_id, {})
            key = UpdateState._source_key(url)
            previous = feeds.get(key, {}).get("records", {})
            _, changes, selected = select_candidates(previous, snapshot, scope)
            return key not in feeds, changes, selected

    def validate(self) -> None:
        with self.lock(Path("workflow.lock")):
            self._read()

    def complete(self, plugin_id: str, source_url: str, target_url: str) -> None:
        with self.lock(Path("workflow.lock")):
            document = self._read()
            records = document["sources"][plugin_id][UpdateState._source_key(source_url)]["records"]
            for record in records.values():
                if record["url"] == target_url:
                    record["completed"] = True
            self._write(document)

    def _write(self, document: dict[str, Any]) -> None:
        self.filesystem.write_bytes_atomic(self.relative, json.dumps(document, ensure_ascii=False).encode("utf-8"))


def select_candidates(
    previous: dict[str, Any], snapshot: UpdateSnapshot, scope: str
) -> tuple[dict[str, Any], tuple[UpdateChange, ...], dict[str, tuple[str, ...]]]:
    """Pure initial-round selection, shared by preparation and preview."""
    current: dict[str, Any] = {}
    changes: list[UpdateChange] = []
    selected: dict[str, tuple[str, ...]] = {}
    for candidate in snapshot.candidates:
        identity = UpdateState._identity(candidate)
        if identity in current:
            raise PluginError("check_updates returned duplicate candidate identities")
        old = previous.get(identity)
        changed = old is None or old["url"] != candidate.url or old["revision"] != candidate.revision
        reason = "added" if old is None else "changed" if changed else "unfinished"
        if changed:
            changes.append(
                UpdateChange(UpdateChangeKind(reason), candidate.url, candidate.content_id, candidate.revision)
            )
        completed = not changed and old is not None and old["completed"]
        if scope == "all" or not completed:
            reasons = selected.get(candidate.url, ())
            chosen_reason = "all" if scope == "all" else reason
            selected[candidate.url] = tuple(dict.fromkeys((*reasons, chosen_reason)))
        current[identity] = {
            "url": candidate.url,
            "content_id": candidate.content_id,
            "revision": candidate.revision,
            "completed": completed,
        }
    for identity, old in previous.items():
        if identity not in current:
            changes.append(UpdateChange(UpdateChangeKind.REMOVED, old["url"], old["content_id"], old["revision"]))
    # Any candidate sharing a selected URL participates in that attempt.
    for record in current.values():
        if record["url"] in selected:
            record["completed"] = False
    selected = {
        candidate.url: selected[candidate.url] for candidate in snapshot.candidates if candidate.url in selected
    }
    return current, tuple(changes), selected
