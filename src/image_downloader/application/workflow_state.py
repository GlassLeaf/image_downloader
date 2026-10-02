"""Offline workflow state and history access without composing a runtime."""

from __future__ import annotations

from datetime import datetime

from ..configuration.models import AppConfig
from ..configuration.paths import resolve_paths
from ..exceptions import UpdateStateError
from ..models import UpdateCandidate, WorkflowPruneResult, WorkflowRunRecord, WorkflowStateItem, WorkflowStateView
from ..privacy.log_safety import safe_url
from ..storage.filesystem import FileSystem
from ..storage.state import UpdateState
from ..storage.workflow import WorkflowState
from ..storage.workflow_history import WorkflowHistoryStore, parse_time


class WorkflowStateService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        filesystem = FileSystem(resolve_paths(config)["state"])
        self._state = WorkflowState(filesystem)
        self._history = WorkflowHistoryStore(filesystem, config.workflow_history)

    def list_workflows(self, *, plugin_id: str | None = None) -> tuple[WorkflowStateView, ...]:
        # Fixed lock order. Readers do not acquire the long-lived feed lock.
        with self._state.lock(self._history.relative.with_name("workflow.lock")):
            state = self._state._read()
            runs = self._history.visible()
        latest: dict[tuple[str | None, str], WorkflowRunRecord] = {}
        for run in runs:
            latest[run.plugin_id, run.feed_key] = run
        views: dict[tuple[str | None, str], WorkflowStateView] = {}
        for plugin, feeds in state["sources"].items():
            if plugin_id is not None and plugin != plugin_id:
                continue
            for key, feed in feeds.items():
                latest_run = latest.get((plugin, key))
                views[plugin, key] = WorkflowStateView(
                    plugin,
                    key,
                    latest_run.source_url if latest_run else None,
                    _checked_at(feed["checked_at"]),
                    tuple(
                        WorkflowStateItem(
                            UpdateCandidate(safe_url(r["url"]), r["content_id"], r["revision"]), r["completed"]
                        )
                        for r in feed["records"].values()
                    ),
                    latest_run,
                )
        for (plugin, key), run in latest.items():
            if (plugin, key) not in views and (plugin_id is None or plugin_id == plugin):
                views[plugin, key] = WorkflowStateView(plugin, key, run.source_url, None, (), run)
        return tuple(sorted(views.values(), key=lambda v: (v.plugin_id or "", v.feed_key)))

    def get_workflow(self, url: str, *, plugin_id: str | None = None) -> tuple[WorkflowStateView, ...]:
        key = UpdateState._source_key(url)
        return tuple(v for v in self.list_workflows(plugin_id=plugin_id) if v.feed_key == key)

    def list_runs(
        self, url: str | None = None, *, plugin_id: str | None = None, limit: int = 20
    ) -> tuple[WorkflowRunRecord, ...]:
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be a positive integer")
        key = UpdateState._source_key(url) if url is not None else None
        runs = self._history.visible()
        return tuple(
            r
            for r in reversed(runs)
            if (key is None or r.feed_key == key) and (plugin_id is None or r.plugin_id == plugin_id)
        )[:limit]

    def get_run(self, run_id: str) -> WorkflowRunRecord | None:
        return next((r for r in self._history.visible() if r.run_id == run_id), None)

    def prune_history(self, *, dry_run: bool = False) -> WorkflowPruneResult:
        if type(dry_run) is not bool:
            raise TypeError("dry_run must be bool")
        return self._history.prune(dry_run=dry_run)


def _checked_at(value: str) -> datetime:
    try:
        return parse_time(value)
    except (ValueError, OverflowError) as exc:
        raise UpdateStateError("workflow state has an invalid timestamp") from exc
