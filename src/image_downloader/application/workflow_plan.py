"""Read-only initial workflow selection with a detached authentication session."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING, Literal

from ..exceptions import error_info_for
from ..models import WorkflowPlanItem, WorkflowPlanResult
from ..plugins.plugin_invoker import PluginInvoker
from ..plugins.plugin_manifest import PluginConfigOverrides, PluginDownloadPolicyOverrides
from ..storage.workflow import WorkflowState, finish_transaction
from ..transport.gateway import RequestGateway

if TYPE_CHECKING:
    from .service import DownloadService


async def plan_workflow(
    service: DownloadService,
    url: str,
    scope: Literal["all", "updated"],
    overrides: PluginConfigOverrides | None,
    fallback: bool | None,
    plugin_id: str | None,
    force_plugin: bool,
    policies: PluginDownloadPolicyOverrides | None,
) -> WorkflowPlanResult:
    result = WorkflowPlanResult(url, scope)
    try:
        record, plugin = service._select_site_plugin(url, overrides, fallback, plugin_id, force_plugin)
        result = replace(result, plugin_id=record.id)
        owned = True
        try:
            state = WorkflowState(service.state.filesystem)
            lock = state.feed_lock(record.id, url)
            await lock.acquire_async()
            try:
                await finish_transaction(state.validate)
                await finish_transaction(service.state._validate_read_only)
                gateway = RequestGateway(
                    service.config, service.cookie_store.clone_jar(service.gateway.client.cookies.jar)
                )
                primary_error: BaseException | None = None
                try:
                    # _site_operation owns cleanup even if operation construction fails.
                    owned = False
                    async with service._site_operation(
                        url,
                        overrides,
                        fallback,
                        plugin_id,
                        force_plugin,
                        policies,
                        request_gateway=gateway,
                        diagnostics_enabled=False,
                        selected_plugin=(record, plugin),
                    ) as (_, _, context, _, _, invoker):
                        snapshot = await service._fetch_update_snapshot(plugin, url, context, invoker)
                        result = replace(result, snapshot=snapshot)
                        await finish_transaction(service.state._validate_read_only)
                        first, changes, selected = await finish_transaction(
                            partial(state.preview, record.id, url, snapshot, scope)
                        )
                        result = replace(
                            result,
                            first_run=first,
                            changes=changes,
                            items=tuple(
                                WorkflowPlanItem(c, c.url in selected, selected.get(c.url, ("completed",)))
                                for c in snapshot.candidates
                            ),
                        )
                except BaseException as primary:
                    primary_error = primary
                    raise
                finally:
                    try:
                        await gateway.close()
                    except BaseException:
                        if primary_error is None:
                            raise
            except BaseException as primary:
                if owned:
                    owned = False
                    await service._cleanup_site_after_failure(PluginInvoker(record.id), plugin, primary, None)
                raise
            finally:
                lock.release()
        except BaseException as primary:
            if owned:
                await service._cleanup_site_after_failure(PluginInvoker(record.id), plugin, primary, None)
            raise
        return result
    except (Exception, asyncio.CancelledError) as exc:
        result = replace(
            result,
            stop_error=None if isinstance(exc, asyncio.CancelledError) else error_info_for(exc),
            cancelled=isinstance(exc, asyncio.CancelledError),
        )
        exc.__dict__["workflow_plan_result"] = result
        raise
