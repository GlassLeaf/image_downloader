"""Plan and atomically apply URL state changes after interactive approval."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator
from contextlib import ExitStack
from dataclasses import asdict, replace
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, cast

from ..exceptions import UpdateStateError, WorkflowStateConflictError
from ..models import (
    UpdateCandidate,
    UpdateSnapshot,
    WorkflowTargetChange,
    WorkflowTargetChangePlan,
    WorkflowTargetChangeResult,
    WorkflowTargetRecord,
    WorkflowTargetSelector,
    WorkflowTargetStatus,
)
from .source_identity import source_key
from .workflow import WorkflowState, select_candidates
from .workflow_history import parse_time


def _validate_timestamp(feed: dict[str, Any]) -> None:
    try:
        parse_time(feed["checked_at"])
    except (ValueError, OverflowError) as exc:
        raise UpdateStateError("workflow state has an invalid timestamp") from exc


def _record(plugin: str, key: str, identity: str, value: dict[str, Any]) -> WorkflowTargetRecord:
    return WorkflowTargetRecord(
        plugin, key, identity, UpdateCandidate(value["url"], value["content_id"], value["revision"]), value["status"]
    )


def _matches(row: WorkflowTargetRecord, selector: WorkflowTargetSelector, pattern: re.Pattern[str] | None) -> bool:
    return (
        (selector.url is None or row.candidate.url == selector.url)
        and (pattern is None or pattern.search(row.candidate.url) is not None)
        and (selector.from_status is None or row.status == selector.from_status)
    )


def _feeds(document: dict[str, Any], selector: WorkflowTargetSelector) -> Iterator[tuple[str, str, dict[str, Any]]]:
    key_filter = source_key(selector.source_url) if selector.source_url is not None else None
    for plugin, feeds in sorted(document["sources"].items()):
        if selector.plugin_id is not None and plugin != selector.plugin_id:
            continue
        for key, feed in sorted(feeds.items()):
            if key_filter is None or key == key_filter:
                _validate_timestamp(feed)
                yield plugin, key, feed


def _rows(document: dict[str, Any], selector: WorkflowTargetSelector) -> tuple[WorkflowTargetRecord, ...]:
    pattern = re.compile(selector.url_regex) if selector.url_regex is not None else None
    rows = (
        _record(plugin, key, identity, value)
        for plugin, key, feed in _feeds(document, selector)
        for identity, value in sorted(feed["records"].items())
    )
    return tuple(row for row in rows if _matches(row, selector, pattern))


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def change_result(
    plan: WorkflowTargetChangePlan, approved_urls: Iterable[str] = (), *, dry_run: bool = False
) -> WorkflowTargetChangeResult:
    approved = frozenset(approved_urls)
    pending = tuple(change for change in plan.changes if change.requires_write)
    pending_urls = {change.after.candidate.url for change in pending}
    if not approved <= pending_urls:
        raise ValueError("approval contains a URL outside the change plan")
    accepted = sum(change.after.candidate.url in approved for change in pending)
    return WorkflowTargetChangeResult(
        matched_urls=len({change.after.candidate.url for change in plan.changes}),
        matched_records=len(plan.changes),
        planned_records=len(pending),
        approved_urls=len(approved),
        approved_records=accepted,
        skipped_urls=0 if dry_run else len(pending_urls - approved),
        skipped_records=0 if dry_run else len(pending) - accepted,
        unchanged_records=len(plan.changes) - len(pending),
        changed_records=0 if dry_run else accepted,
        dry_run=dry_run,
    )


class WorkflowTargetStore:
    def __init__(self, state: WorkflowState) -> None:
        self.state = state

    def list_targets(self, selector: WorkflowTargetSelector) -> tuple[WorkflowTargetRecord, ...]:
        with self.state.lock(Path("workflow.lock")):
            return _rows(self.state._read(), selector)

    def plan_changes(
        self,
        selector: WorkflowTargetSelector,
        status: WorkflowTargetStatus | None,
        *,
        snapshot: UpdateSnapshot | None = None,
        plugin_id: str | None = None,
        download_scope: str = "updated",
    ) -> WorkflowTargetChangePlan:
        if status not in (None, "completed", "pending", "cancelled"):
            raise ValueError("invalid workflow target status")
        if download_scope not in ("all", "updated"):
            raise ValueError("invalid download scope")
        if snapshot is not None and (selector.source_url is None or plugin_id is None):
            raise ValueError("refresh requires source URL and resolved plugin ID")
        if snapshot is not None and selector.plugin_id not in (None, plugin_id):
            raise ValueError("refresh plugin does not match selector")
        with self.state.lock(Path("workflow.lock")):
            return self._plan(self.state._read(), selector, status, snapshot, plugin_id, download_scope)

    def _plan(
        self,
        document: dict[str, Any],
        selector: WorkflowTargetSelector,
        status: WorkflowTargetStatus | None,
        snapshot: UpdateSnapshot | None,
        plugin_id: str | None,
        download_scope: str,
    ) -> WorkflowTargetChangePlan:
        if snapshot is None:
            rows = _rows(document, selector)
            changes = tuple(
                WorkflowTargetChange(row, row if status is None else replace(row, status=status)) for row in rows
            )
            baseline = _digest([asdict(row) for row in rows])
        else:
            assert selector.source_url is not None and plugin_id is not None
            key = source_key(selector.source_url)
            feed = document["sources"].get(plugin_id, {}).get(key)
            if feed is not None:
                _validate_timestamp(feed)
            previous = feed["records"] if feed is not None else {}
            current, _, selected = select_candidates(previous, snapshot, download_scope, mark_selected_pending=False)
            pattern = re.compile(selector.url_regex) if selector.url_regex is not None else None
            changes = tuple(
                WorkflowTargetChange(
                    _record(plugin_id, key, identity, previous[identity]) if identity in previous else None,
                    row if status is None else replace(row, status=status),
                )
                for identity, value in current.items()
                if (row := _record(plugin_id, key, identity, value)).candidate.url in selected
                and _matches(row, selector, pattern)
            )
            baseline = _digest(feed)
        return WorkflowTargetChangePlan(
            selector, status, changes, baseline, snapshot, plugin_id, cast(Literal["all", "updated"], download_scope)
        )

    def apply_changes(self, plan: WorkflowTargetChangePlan, approved_urls: Iterable[str]) -> WorkflowTargetChangeResult:
        approved = frozenset(approved_urls)
        if plan.status is None:
            raise ValueError("a listing plan cannot be applied")
        result = change_result(plan, approved)
        if not approved:
            return result
        # Never hold workflow.lock while waiting for a long-lived feed lock.
        feeds = sorted({(c.after.plugin_id, c.after.feed_key) for c in plan.changes})
        with ExitStack() as stack:
            for plugin, key in feeds:
                stack.enter_context(self.state.feed_key_lock(plugin, key))
            with self.state.lock(Path("workflow.lock")):
                document = self.state._read()
                current = self._plan(
                    document, plan.selector, plan.status, plan.snapshot, plan.plugin_id, plan.download_scope
                )
                if current != plan:
                    raise WorkflowStateConflictError()
                for change in plan.changes:
                    row = change.after
                    if not change.requires_write or row.candidate.url not in approved:
                        continue
                    feeds_document = document["sources"].setdefault(row.plugin_id, {})
                    if plan.snapshot is not None:
                        feed = feeds_document.setdefault(row.feed_key, {"records": {}})
                        feed["checked_at"] = plan.snapshot.checked_at.isoformat()
                    else:
                        feed = feeds_document[row.feed_key]
                    feed["records"][row.identity] = {**asdict(row.candidate), "status": row.status}
                self.state._write(document)
        return result
