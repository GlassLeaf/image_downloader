"""Update checking and state publication."""

from __future__ import annotations

import asyncio

from ..exceptions import (
    UpdateCheckUnsupportedError,
    error_info_for,
)
from ..models import (
    UpdateChangeKind,
    UpdateResult,
    UpdateSnapshot,
)
from ..observability.diagnostic_safety import best_effort_diagnostic
from ..observability.events import EventBus, EventName, EventPayload
from ..observability.logging import DownloadLogger
from ..observability.scope import OperationDiagnosticsScope
from ..plugins.plugin_invoker import PluginInvoker
from ..ports import PluginExecutionContext, SitePlugin, UpdateProvider
from ..storage.state import UpdateState
from .requests import UpdateCheckRequest
from .site_operation import SiteOperationFactory, SiteSelection


class UpdateExecutor:
    def __init__(
        self, state: UpdateState, events: EventBus, logger: DownloadLogger, operations: SiteOperationFactory
    ) -> None:
        self.state, self.events, self.logger, self.operations = state, events, logger, operations

    async def execute(
        self,
        request: UpdateCheckRequest,
        diagnostics: OperationDiagnosticsScope,
        selected_plugin: SiteSelection | None = None,
    ) -> tuple[UpdateResult, UpdateSnapshot]:
        try:
            async with self.operations.open(
                request.url,
                request.plugin_overrides,
                request.fallback_override,
                request.plugin_id,
                request.force_plugin,
                request.plugin_download_policy_overrides,
                selected_plugin=selected_plugin,
            ) as site:
                record, plugin, context, invoker = site.record, site.plugin, site.context, site.invoker
                diagnostics.capture(self.operations.python_log_namespaces(record, include_processors=False))
                await best_effort_diagnostic(
                    self.logger.core,
                    "operation_started",
                    module="runtime",
                    url=request.url,
                    plugin_id=record.id,
                    action="check_updates",
                    debug=True,
                )
                await best_effort_diagnostic(
                    self.logger.core,
                    "plugin_selected",
                    module="plugin",
                    url=request.url,
                    plugin_id=record.id,
                    action="check_updates",
                    debug=True,
                )
                snapshot = await self.fetch_snapshot(plugin, request.url, context, invoker)
                changes = await self.state.apply_snapshot_async(record.id, request.url, snapshot)
                for change in changes:
                    if change.kind in {UpdateChangeKind.ADDED, UpdateChangeKind.CHANGED}:
                        await self.events.emit(EventName.UPDATED_URL_FOUND, EventPayload(url=change.url))
                await best_effort_diagnostic(
                    self.logger.core,
                    "response_received",
                    module="download",
                    url=request.url,
                    count=len(changes),
                    plugin_id=record.id,
                    debug=True,
                )
                return UpdateResult(request.url, record.id, changes, snapshot.checked_at), snapshot
        except asyncio.CancelledError:
            await best_effort_diagnostic(
                self.logger.core,
                "download_cancelled",
                module="runtime",
                url=request.url,
                action="check_updates",
                debug=True,
            )
            raise
        except Exception as exc:
            info = error_info_for(exc)
            await self.events.emit(
                EventName.UPDATE_FAILED,
                EventPayload(
                    url=request.url,
                    response_url=info.response_url,
                    path=info.output_path,
                    http_status=info.http_status,
                    error_code=info.code,
                    error_reason=info.reason,
                    error_class=info.exception,
                    operation="update",
                ),
            )
            await best_effort_diagnostic(
                self.logger.core,
                "operation_failed",
                module="runtime",
                url=request.url,
                error=exc,
                action="check_updates",
                debug=True,
            )
            await best_effort_diagnostic(self.logger.error_detail, exc, url=request.url, module="runtime")
            raise

    async def fetch_snapshot(
        self, plugin: SitePlugin, url: str, context: PluginExecutionContext, invoker: PluginInvoker
    ) -> UpdateSnapshot:
        if not isinstance(plugin, UpdateProvider):
            raise UpdateCheckUnsupportedError()
        return await invoker.check_updates(plugin, url, context)
