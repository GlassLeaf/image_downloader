"""Workflow coordination, refresh rounds, and invocation-local image retries."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from ..configuration.models import ImageFormat
from ..configuration.paths import resolve_paths
from ..exceptions import (
    AuthenticationError,
    ConfigurationError,
    ExistingFileConflictError,
    ImageProcessingError,
    PluginError,
    RequestError,
    StorageError,
    WorkflowRetryTimeoutError,
    error_info_for,
)
from ..models import (
    UpdateChange,
    UpdateResult,
    UpdateSnapshot,
    WorkflowAttemptResult,
    WorkflowItemResult,
    WorkflowResult,
    WorkflowRoundResult,
)
from ..observability.diagnostic_safety import best_effort_diagnostic
from ..observability.events import EventName, EventPayload
from ..observability.scope import OperationDiagnosticsScope
from ..plugins.lifecycle import PluginRecord
from ..plugins.plugin_manifest import PluginConfigOverrides, PluginDownloadPolicyOverrides
from ..ports import SitePlugin
from ..storage.workflow import WorkflowState, finish_transaction
from ..storage.workflow_logging import WorkflowLogWriter, item_summary
from .workflow_recording import _settle, record_result, revise_result
from .workflow_reporting import stopped_status, workflow_outcome, workflow_status
from .workflow_retry import ImageLedger

if TYPE_CHECKING:
    from .service import DownloadService


class _WorkflowExecution:
    def __init__(
        self,
        service: DownloadService,
        url: str,
        scope: Literal["all", "updated"],
        overrides: PluginConfigOverrides | None,
        fallback: bool | None,
        plugin_id: str | None,
        force_plugin: bool,
        policies: PluginDownloadPolicyOverrides | None,
        image_format: ImageFormat | None,
        retries: int,
        delay: float,
        timeout: float | None,
        progress_log: bool | None,
    ) -> None:
        self.service, self.url, self.scope = service, url, scope
        self.overrides, self.fallback, self.plugin_id = overrides, fallback, plugin_id
        self.force_plugin, self.policies, self.image_format = force_plugin, policies, image_format
        self.retries, self.delay, self.timeout = retries, delay, timeout
        self.state = WorkflowState(service.state.filesystem)
        self.snapshot: UpdateSnapshot | None = None
        self.changes: tuple[UpdateChange, ...] = ()
        self.items: dict[str, WorkflowItemResult] = {}
        self.ledgers: dict[str, ImageLedger] = {}
        self.rounds: list[WorkflowRoundResult] = []
        self.reported = False
        self.run_id = str(uuid4())
        self.started_at = datetime.now(UTC)
        self.feed_plugin_id: str | None = None
        self.log = WorkflowLogWriter(
            resolve_paths(service.config)["logs"] / "workflow",
            self.run_id,
            self.started_at,
            service.config.workflow_logging.progress_enabled if progress_log is None else progress_log,
        )

    def result(self, error: BaseException | None = None) -> WorkflowResult:
        return WorkflowResult(
            self.url,
            self.scope,
            self.snapshot,
            self.changes,
            tuple(self.items.values()),
            error_info_for(error) if error is not None and not isinstance(error, asyncio.CancelledError) else None,
            isinstance(error, asyncio.CancelledError),
            self.retries,
            self.delay,
            self.timeout,
            tuple(self.rounds),
            isinstance(error, WorkflowRetryTimeoutError),
            run_id=self.run_id,
            workflow_log=self.log.result,
        )

    def pending(self) -> bool:
        return any(
            item.status in {"partial", "failed", "unprocessed"} and self.ledgers[url].needs_retry(item.error)
            for url, item in self.items.items()
        )

    async def run(self) -> WorkflowResult:
        await finish_transaction(
            partial(
                self.log.start,
                {
                    "source_url": self.url,
                    "download_scope": self.scope,
                    "started_at": self.started_at.isoformat(),
                    "workflow_retries": self.retries,
                    "workflow_retry_delay": self.delay,
                    "workflow_retry_timeout": self.timeout,
                },
            )
        )
        record, plugin = self.service._select_site_plugin(
            self.url, self.overrides, self.fallback, self.plugin_id, self.force_plugin
        )
        self.feed_plugin_id = record.id
        lock = self.state.feed_lock(record.id, self.url)
        await lock.acquire_async()
        try:
            await finish_transaction(self.state._read)
            await self.round(0, (record, plugin))
            if self.pending() and self.retries:
                timeout = asyncio.timeout(self.timeout)
                try:
                    async with timeout:
                        for number in range(1, self.retries + 1):
                            if not self.pending():
                                break
                            await finish_transaction(
                                partial(
                                    self.log.event,
                                    "retry_wait",
                                    {
                                        "round_number": number,
                                        "run_number": number + 1,
                                        "delay": self.delay,
                                    },
                                )
                            )
                            await best_effort_diagnostic(
                                self.service.logger.core,
                                "workflow_retry_wait",
                                module="workflow",
                                url=self.url,
                                count=number,
                                debug=True,
                            )
                            await asyncio.sleep(self.delay)
                            await self.round(number, (record, plugin))
                except TimeoutError as exc:
                    if not timeout.expired():
                        raise
                    error = WorkflowRetryTimeoutError()
                    if self.rounds[-1].round_number == number:
                        self.rounds[-1] = replace(self.rounds[-1], status="stopped", error=error_info_for(error))
                    else:
                        self.rounds.append(
                            WorkflowRoundResult(number, None, status="stopped", error=error_info_for(error))
                        )
                    self.reported = False
                    raise error from exc
        finally:
            lock.release()
        return self.result()

    async def check(self, selected_plugin: tuple[PluginRecord, SitePlugin]) -> tuple[UpdateResult, UpdateSnapshot]:
        service = self.service
        async with OperationDiagnosticsScope(
            service.logger, lambda: service.notifications.flush(source_url=self.url)
        ) as diagnostics:
            await service.events.emit(EventName.UPDATE_CHECK_STARTED, EventPayload(url=self.url))
            try:
                self.reported = True
                update, snapshot = await service._check_updates_operation(
                    self.url,
                    self.overrides,
                    self.fallback,
                    self.plugin_id,
                    self.force_plugin,
                    self.policies,
                    diagnostics,
                    selected_plugin=selected_plugin,
                )
            finally:
                await service.events.emit(EventName.UPDATE_CHECK_FINISHED, EventPayload(url=self.url))
        self.reported = False
        return update, snapshot

    def prepare(self, plugin_id: str, snapshot: UpdateSnapshot, number: int) -> dict[str, tuple[str, ...]]:
        # Publish the committed preparation even when its awaiter is cancelled.
        history_absent, changes, selected = self.state.prepare_with_history(
            plugin_id, self.url, snapshot, self.scope if number == 0 else "updated"
        )
        retry_urls = {
            url
            for url in selected
            if url in self.items
            and self.items[url].status in {"partial", "failed", "unprocessed"}
            and self.ledgers[url].needs_retry(self.items[url].error)
        }
        if number:
            selected = {
                url: reasons
                for url, reasons in selected.items()
                if reasons != ("unfinished",)
                or url not in self.items
                or self.items[url].status == "removed"
                or self.ledgers[url].needs_retry(self.items[url].error)
            }
        current = {candidate.url for candidate in snapshot.candidates}
        removed = tuple(url for url, item in self.items.items() if url not in current and item.status != "removed")
        for url in removed:
            self.items[url] = replace(self.items[url], status="removed", error=None)
            del self.ledgers[url]
        for url, reasons in selected.items():
            old = self.items.get(url)
            if old is None or old.status == "removed":
                self.ledgers[url] = ImageLedger(url)
                self.items[url] = WorkflowItemResult(url, reasons, "unprocessed", attempts=old.attempts if old else ())
            else:
                self.items[url] = replace(
                    old, status="unprocessed", reasons=tuple(dict.fromkeys((*old.reasons, *reasons)))
                )
        self.snapshot, self.changes = snapshot, changes
        self.rounds.append(WorkflowRoundResult(number, snapshot, changes, tuple(selected), removed))
        self.log.plan(
            number,
            {
                "source_url": self.url,
                "plugin_id": plugin_id,
                "download_scope": self.scope,
                "workflow_history_absent": history_absent,
                "candidates": len(snapshot.candidates),
                "selected_urls": [
                    {"url": url, "reasons": reasons, "retry": bool(number and url in retry_urls)}
                    for url, reasons in selected.items()
                ],
                "selected_url_count": len(selected),
                "changes": [
                    {
                        "kind": change.kind.value,
                        "url": change.url,
                        "content_id": change.content_id,
                        "revision": change.revision,
                    }
                    for change in changes
                ],
            },
        )
        for url in dict.fromkeys((*removed, *(change.url for change in changes if change.kind.value == "removed"))):
            self.log.event("target_removed", {"url": url, "round_number": number, "download_again": False})
        return selected

    async def round(self, number: int, selected_plugin: tuple[PluginRecord, SitePlugin]) -> None:
        try:
            await finish_transaction(
                partial(
                    self.log.event,
                    "round_started",
                    {
                        "round_number": number,
                        "run_number": number + 1,
                    },
                )
            )
            if number:
                fresh = self.service._select_site_plugin(
                    self.url, self.overrides, self.fallback, self.plugin_id, self.force_plugin
                )
                if fresh[0].id != selected_plugin[0].id:
                    raise ConfigurationError("workflow feed plugin changed while its lock was held")
                selected_plugin = fresh
                await best_effort_diagnostic(
                    self.service.logger.core,
                    "workflow_retry_started",
                    module="workflow",
                    url=self.url,
                    count=number,
                    debug=True,
                )
            update, snapshot = await self.check(selected_plugin)
            selected = await finish_transaction(partial(self.prepare, update.plugin_id, snapshot, number))
            for url, reasons in selected.items():
                self.reported = True
                self.items[url] = await self.download(self.items[url], number, reasons)
                self.reported = False
                if self.items[url].status == "success":
                    await finish_transaction(partial(self.state.complete, update.plugin_id, self.url, url))
        except BaseException as exc:
            error = None if isinstance(exc, asyncio.CancelledError) else error_info_for(exc)
            if self.rounds and self.rounds[-1].round_number == number:
                self.rounds[-1] = replace(self.rounds[-1], status="stopped", error=error)
            else:
                self.rounds.append(WorkflowRoundResult(number, None, status="stopped", error=error))
            raise

    async def download(self, item: WorkflowItemResult, number: int, reasons: tuple[str, ...]) -> WorkflowItemResult:
        service = self.service
        ledger = self.ledgers[item.url]
        ledger.start_attempt()
        error = None
        fatal: BaseException | None = None
        status: Literal["success", "partial", "failed", "unprocessed"] = "success"
        try:
            await finish_transaction(
                partial(
                    self.log.event,
                    "url_started",
                    {
                        "url": item.url,
                        "round_number": number,
                        "reasons": reasons,
                    },
                )
            )
            async with OperationDiagnosticsScope(
                service.logger, lambda: service.notifications.flush(source_url=item.url)
            ) as diagnostics:
                with service.notifications.defer_download():
                    try:
                        await service.events.emit(EventName.BEFORE_DOWNLOAD, EventPayload(url=item.url))
                        await service._run_operation(
                            item.url,
                            self.overrides,
                            self.fallback,
                            self.plugin_id,
                            self.force_plugin,
                            self.policies,
                            self.image_format,
                            diagnostics,
                            ledger,
                        )
                    finally:
                        await service.events.emit(EventName.AFTER_DOWNLOAD, EventPayload(url=item.url))
        except (
            AuthenticationError,
            PluginError,
            RequestError,
            ImageProcessingError,
            ExistingFileConflictError,
        ) as exc:
            error, status = error_info_for(exc), "failed"
        except BaseException as exc:
            fatal = exc
            error = None if isinstance(exc, asyncio.CancelledError) else error_info_for(exc)
            status = "unprocessed" if isinstance(exc, asyncio.CancelledError) else "failed"
        # Observer finalization can be cancelled after files have already committed.
        # Publish the settled ledger before propagating every operation failure.
        download = ledger.result()
        if status == "success" and download is not None and download.failures:
            status = "partial"
        attempt = WorkflowAttemptResult(number, status, download, error, ledger.images())
        result = replace(item, status=status, download=download, error=error, attempts=(*item.attempts, attempt))
        self.items[item.url] = result
        # Commit the ledger first, then settle recording even during cancellation.
        try:
            await finish_transaction(
                partial(
                    self.log.event,
                    "url_interrupted" if fatal is not None else "url_finished",
                    {
                        "round_number": number,
                        "interrupted": fatal is not None,
                        **item_summary(result),
                        "reasons": reasons,
                    },
                )
            )
        except asyncio.CancelledError:
            if fatal is None:
                raise
        if fatal is not None:
            raise fatal
        return result


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
    retries: int = 1,
    delay: float = 600.0,
    timeout: float | None = None,
    progress_log: bool | None = None,
) -> WorkflowResult:
    execution = _WorkflowExecution(
        service,
        url,
        scope,
        overrides,
        fallback,
        plugin_id,
        force_plugin,
        policies,
        image_format,
        retries,
        delay,
        timeout,
        progress_log,
    )
    failure: BaseException | None = None
    try:
        try:
            await execution.run()
        except Exception as exc:
            if not execution.reported:
                await _report_workflow_failure(service, url, exc)
            raise
        finally:
            for item in execution.items.values():
                await best_effort_diagnostic(service.notifications.workflow_item, item)
    except BaseException as exc:
        failure = exc
    result = execution.result(failure)
    status = stopped_status(failure) if failure is not None and not result.timed_out else workflow_status(result)
    try:
        result = await record_result(
            service.config,
            result,
            execution.feed_plugin_id,
            execution.started_at,
            status,
            service.outputs.root,
        )
    except asyncio.CancelledError as exc:
        result = getattr(
            exc,
            "workflow_result",
            replace(
                result,
                cancelled=True,
                stop_error=None,
                history_saved=False,
                history_warning="workflow history could not be saved; execution result is unchanged",
            ),
        )
        failure = exc
    # History persistence and dedicated progress recording are independent.
    cancellation = await _settle(
        partial(
            execution.log.finish,
            result,
            stopped_status(failure) if failure is not None and not result.timed_out else workflow_status(result),
            workflow_outcome(result),
        )
    )
    if cancellation is not None:
        failure = cancellation
        result = replace(result, cancelled=True, stop_error=None)
        try:
            result = await revise_result(service.config, result, 130, service.outputs.root)
        except asyncio.CancelledError as exc:
            result = getattr(exc, "workflow_result", result)
        await _settle(partial(execution.log.finish, result, 130, workflow_outcome(result), correction=True))
    result = replace(result, workflow_log=execution.log.result)
    if failure is not None:
        failure.__dict__["workflow_result"] = result
        raise failure
    return result


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
