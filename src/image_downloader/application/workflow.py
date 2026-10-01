"""Workflow coordination using the service's existing operation boundaries."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING, Literal

from ..configuration.models import ImageFormat
from ..exceptions import (
    AuthenticationError,
    ConfigurationError,
    ExistingFileConflictError,
    PluginError,
    RequestError,
    StorageError,
    error_info_for,
)
from ..models import UpdateChange, UpdateSnapshot, WorkflowItemResult, WorkflowResult
from ..observability.diagnostic_safety import best_effort_diagnostic
from ..observability.events import EventName, EventPayload
from ..observability.scope import OperationDiagnosticsScope
from ..plugins.plugin_manifest import PluginConfigOverrides, PluginDownloadPolicyOverrides
from ..storage.workflow import WorkflowState, finish_transaction

if TYPE_CHECKING:
    from .service import DownloadService


async def execute_workflow(
    service: DownloadService,
    url: str,
    scope: Literal["all", "updated"],
    overrides: PluginConfigOverrides | None,
    fallback: bool | None,
    plugin_id: str | None,
    force_plugin: bool,
    policies: PluginDownloadPolicyOverrides | None,
    image_format: ImageFormat | None,
) -> WorkflowResult:
    state = WorkflowState(service.state.filesystem)
    snapshot: UpdateSnapshot | None = None
    changes: tuple[UpdateChange, ...] = ()
    items: list[WorkflowItemResult] = []
    prepared: list[tuple[tuple[UpdateChange, ...], dict[str, tuple[str, ...]]]] = []
    reported = False
    try:
        record, plugin = service._select_site_plugin(url, overrides, fallback, plugin_id, force_plugin)
        lock = state.feed_lock(record.id, url)
        await lock.acquire_async()
        try:
            # Validate persisted workflow state before modifying the existing update history.
            await finish_transaction(state._read)
            async with OperationDiagnosticsScope(
                service.logger, lambda: service.notifications.flush(source_url=url)
            ) as diagnostics:
                await service.events.emit(EventName.UPDATE_CHECK_STARTED, EventPayload(url=url))
                try:
                    reported = True
                    update, snapshot = await service._check_updates_operation(
                        url,
                        overrides,
                        fallback,
                        plugin_id,
                        force_plugin,
                        policies,
                        diagnostics,
                        selected_plugin=(record, plugin),
                    )
                finally:
                    await service.events.emit(EventName.UPDATE_CHECK_FINISHED, EventPayload(url=url))
            reported = False
            await finish_transaction(lambda: prepared.append(state.prepare(update.plugin_id, url, snapshot, scope)))
            changes, selected = prepared[0]
            items = [WorkflowItemResult(target, reasons, "unprocessed") for target, reasons in selected.items()]
            for index, item in enumerate(items):
                try:
                    reported = True
                    items[index] = await _download_item(
                        service, item, overrides, fallback, plugin_id, force_plugin, policies, image_format
                    )
                except Exception as exc:
                    items[index] = replace(item, status="failed", error=error_info_for(exc))
                    raise
                reported = False
                if items[index].status == "success":
                    await finish_transaction(partial(state.complete, update.plugin_id, url, item.url))
        finally:
            lock.release()
    except BaseException as exc:
        if isinstance(exc, Exception) and not reported:
            await _report_workflow_failure(service, url, exc)
        if prepared and not items:
            changes, selected = prepared[0]
            items = [WorkflowItemResult(target, reasons, "unprocessed") for target, reasons in selected.items()]
        result = WorkflowResult(
            url,
            scope,
            snapshot,
            changes,
            tuple(items),
            None if isinstance(exc, asyncio.CancelledError) else error_info_for(exc),
            isinstance(exc, asyncio.CancelledError),
        )
        exc.__dict__["workflow_result"] = result
        raise
    return WorkflowResult(url, scope, snapshot, changes, tuple(items))


async def _report_workflow_failure(service: DownloadService, url: str, error: Exception) -> None:
    info = error_info_for(error)
    event = (
        EventName.CONFIG_FAILED
        if isinstance(error, ConfigurationError)
        else EventName.AUTH_FAILED
        if isinstance(error, AuthenticationError)
        else EventName.PLUGIN_FAILED
        if isinstance(error, PluginError)
        else EventName.STORAGE_FAILED
        if isinstance(error, StorageError)
        else EventName.RUNTIME_FAILED
    )
    async with OperationDiagnosticsScope(service.logger, lambda: service.notifications.flush(source_url=url)):
        await service.events.emit(
            event,
            EventPayload(
                url=url,
                error_code=info.code,
                error_reason=info.reason,
                error_class=info.exception,
                operation="workflow",
            ),
        )
        await best_effort_diagnostic(service.logger.error_detail, error, url=url, module="workflow")


async def _download_item(
    service: DownloadService,
    item: WorkflowItemResult,
    overrides: PluginConfigOverrides | None,
    fallback: bool | None,
    plugin_id: str | None,
    force_plugin: bool,
    policies: PluginDownloadPolicyOverrides | None,
    image_format: ImageFormat | None,
) -> WorkflowItemResult:
    async with OperationDiagnosticsScope(
        service.logger, lambda: service.notifications.flush(source_url=item.url)
    ) as diagnostics:
        await service.events.emit(EventName.BEFORE_DOWNLOAD, EventPayload(url=item.url))
        try:
            try:
                result = await service._run_operation(
                    item.url, overrides, fallback, plugin_id, force_plugin, policies, image_format, diagnostics
                )
            except (AuthenticationError, PluginError, RequestError, ExistingFileConflictError) as exc:
                return replace(item, status="failed", error=error_info_for(exc))
            return replace(item, status="partial" if result.failures else "success", download=result)
        finally:
            await service.events.emit(EventName.AFTER_DOWNLOAD, EventPayload(url=item.url))
