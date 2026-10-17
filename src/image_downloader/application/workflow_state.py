"""Offline workflow state and history access without composing a runtime."""

from __future__ import annotations

from ..configuration.models import AppConfig
from ..configuration.paths import resolve_paths
from ..models import UpdateCandidate, WorkflowPruneResult, WorkflowRunRecord, WorkflowStateItem, WorkflowStateView
from ..privacy.log_safety import safe_url
from ..storage.filesystem import FileSystem
from ..storage.source_identity import source_key
from ..storage.workflow import WorkflowState
from ..storage.workflow_history import WorkflowHistoryStore


class WorkflowStateService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        filesystem = FileSystem(resolve_paths(config)["state"])
        self._state = WorkflowState(filesystem)
        self._history = WorkflowHistoryStore(filesystem, config.workflow_history)

    def list_workflows(self, *, plugin_id: str | None = None) -> tuple[WorkflowStateView, ...]:
        # Fixed lock order. Readers do not acquire the long-lived feed lock.
        with self._state.read_feeds(plugin_id=plugin_id) as feeds:
            runs = self._history.visible()
        latest: dict[tuple[str | None, str], WorkflowRunRecord] = {}
        for run in runs:
            latest[run.plugin_id, run.feed_key] = run
        views: dict[tuple[str | None, str], WorkflowStateView] = {}
        for feed in feeds:
            plugin, key = feed.plugin_id, feed.feed_key
            latest_run = latest.get((plugin, key))
            views[plugin, key] = WorkflowStateView(
                plugin,
                key,
                latest_run.source_url if latest_run else None,
                feed.checked_at,
                tuple(
                    WorkflowStateItem(
                        UpdateCandidate(
                            safe_url(item.candidate.url), item.candidate.content_id, item.candidate.revision
                        ),
                        item.completed,
                    )
                    for item in feed.items
                ),
                latest_run,
            )
        for (history_plugin, key), run in latest.items():
            if (history_plugin, key) not in views and (plugin_id is None or plugin_id == history_plugin):
                views[history_plugin, key] = WorkflowStateView(history_plugin, key, run.source_url, None, (), run)
        return tuple(sorted(views.values(), key=lambda v: (v.plugin_id or "", v.feed_key)))

    def get_workflow(self, url: str, *, plugin_id: str | None = None) -> tuple[WorkflowStateView, ...]:
        key = source_key(url)
        return tuple(v for v in self.list_workflows(plugin_id=plugin_id) if v.feed_key == key)

    def list_runs(
        self, url: str | None = None, *, plugin_id: str | None = None, limit: int = 20
    ) -> tuple[WorkflowRunRecord, ...]:
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be a positive integer")
        key = source_key(url) if url is not None else None
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
