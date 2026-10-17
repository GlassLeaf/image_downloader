"""Read-only initial workflow selection with a detached authentication session."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from functools import partial

from ..exceptions import WorkflowPlanCancelledError, WorkflowPlanError, error_info_for
from ..models import WorkflowPlanItem, WorkflowPlanResult
from ..storage.workflow import WorkflowState, finish_transaction
from ..transport.gateway import RequestGateway, closing_gateway
from .requests import WorkflowPlanRequest
from .workflow_runtime import WorkflowRuntime


async def plan_workflow(runtime: WorkflowRuntime, request: WorkflowPlanRequest) -> WorkflowPlanResult:
    url, scope = request.url, request.download_scope
    overrides, fallback = request.plugin_overrides, request.fallback_override
    plugin_id, force_plugin = request.plugin_id, request.force_plugin
    policies = request.plugin_download_policy_overrides
    result = WorkflowPlanResult(url, scope)
    try:
        async with runtime.operations.selection(
            url, overrides, fallback, plugin_id, force_plugin, diagnostics_enabled=False
        ) as selected:
            record, plugin = selected.record, selected.plugin
            result = replace(result, plugin_id=record.id)
            state = WorkflowState(runtime.state.filesystem)
            lock = state.feed_lock(record.id, url)
            await lock.acquire_async()
            try:
                await finish_transaction(state.validate)
                await finish_transaction(runtime.state.validate)
                gateway = RequestGateway(
                    runtime.config, runtime.cookie_store.clone_jar(runtime.gateway.client.cookies.jar)
                )
                async with closing_gateway(gateway):
                    async with runtime.operations.open(
                        url,
                        overrides,
                        fallback,
                        plugin_id,
                        force_plugin,
                        policies,
                        request_gateway=gateway,
                        diagnostics_enabled=False,
                        selected_plugin=selected,
                    ) as operation:
                        context, invoker = operation.context, operation.invoker
                        snapshot = await runtime.updates.fetch_snapshot(plugin, url, context, invoker)
                        result = replace(result, snapshot=snapshot)
                        await finish_transaction(runtime.state.validate)
                        first, changes, selected_urls = await finish_transaction(
                            partial(state.preview, record.id, url, snapshot, scope)
                        )
                        result = replace(
                            result,
                            first_run=first,
                            changes=changes,
                            items=tuple(
                                WorkflowPlanItem(c, c.url in selected_urls, selected_urls.get(c.url, ("completed",)))
                                for c in snapshot.candidates
                            ),
                        )
            except BaseException as primary:
                await runtime.operations.release_selection(selected, primary, diagnostics_enabled=False)
                raise
            finally:
                lock.release()
        return result
    except (Exception, asyncio.CancelledError) as exc:
        result = replace(
            result,
            stop_error=None if isinstance(exc, asyncio.CancelledError) else error_info_for(exc),
            cancelled=isinstance(exc, asyncio.CancelledError),
        )
        if isinstance(exc, asyncio.CancelledError):
            raise WorkflowPlanCancelledError(result) from exc
        raise WorkflowPlanError(result, exc) from exc


class WorkflowPlanner:
    """Preview selection without owning the service's operation lock."""

    def __init__(self, runtime: WorkflowRuntime) -> None:
        self.runtime = runtime

    async def execute(self, request: WorkflowPlanRequest) -> WorkflowPlanResult:
        return await plan_workflow(self.runtime, request)
