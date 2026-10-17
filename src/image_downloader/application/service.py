"""Public operation boundary and ownership of runtime resources."""

from __future__ import annotations

import asyncio
from functools import partial
from types import TracebackType

from ..configuration.models import AppConfig
from ..models import DownloadResult, ManifestInspectionResult, UpdateResult, WorkflowPlanResult, WorkflowResult
from ..observability.events import EventName, EventPayload
from ..observability.scope import OperationDiagnosticsScope
from ..plugins.runtime import PluginRuntime
from ..storage.transactions import finish_transaction, settle_task
from .dependencies import _RuntimeDependencies
from .download_executor import DownloadExecutor
from .inspection import InspectionExecutor
from .requests import DownloadRequest, InspectRequest, UpdateCheckRequest, WorkflowPlanRequest, WorkflowRequest
from .shutdown import close_service, shutdown_resources
from .site_operation import SiteOperationFactory
from .update_executor import UpdateExecutor
from .workflow import WorkflowExecutor
from .workflow_plan import WorkflowPlanner
from .workflow_runtime import WorkflowRuntime


class DownloadService:
    """Serialize public operations and close service-owned resources once."""

    def __init__(
        self,
        config: AppConfig,
        registry: PluginRuntime,
        dependencies: _RuntimeDependencies,
    ) -> None:
        self.config, self.registry = config, registry
        self.outputs = dependencies.outputs
        self.logs = dependencies.logs
        self.state = dependencies.state
        self.events = dependencies.events
        self.logger = dependencies.logger
        self.notifications = dependencies.notifications
        self.cookie_store = dependencies.cookie_store
        self._cookie_baseline = dependencies.cookie_baseline
        self._persist_cookies_on_close = dependencies.persist_cookies_on_close
        self.gateway = dependencies.gateway
        self.image_processor = dependencies.image_processor
        self.output_locks = dependencies.output_locks
        self._operation_lock = asyncio.Lock()
        self._closed = False
        self._close_task: asyncio.Task[BaseException | None] | None = None

        self._operations = SiteOperationFactory(config, registry, self.gateway, self.logger)
        self._downloads = DownloadExecutor(config, registry, dependencies, self._operations)
        self._inspection = InspectionExecutor(config, self.cookie_store, self.gateway, self._operations)
        self._updates = UpdateExecutor(self.state, self.events, self.logger, self._operations)
        workflow_runtime = WorkflowRuntime(
            config,
            self._operations,
            self._downloads,
            self._updates,
            self.outputs,
            self.state,
            self.events,
            self.logger,
            self.notifications,
            self.cookie_store,
            self.gateway,
        )
        self._workflows = WorkflowExecutor(workflow_runtime)
        self._plans = WorkflowPlanner(workflow_runtime)

    async def __aenter__(self) -> DownloadService:
        return self

    async def __aexit__(
        self,
        _exception_type: type[BaseException] | None,
        exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        await close_service(self, primary=exception)

    async def download(self, request: DownloadRequest) -> DownloadResult:
        if not isinstance(request, DownloadRequest):
            raise TypeError("request must be DownloadRequest")
        async with self._operation_lock:
            self._ensure_open()
            async with OperationDiagnosticsScope(
                self.logger,
                lambda: self.notifications.flush(source_url=request.url),
            ) as diagnostics:
                await self.events.emit(EventName.BEFORE_DOWNLOAD, EventPayload(url=request.url))
                try:
                    return await self._downloads.execute(request, diagnostics)
                finally:
                    await self.events.emit(EventName.AFTER_DOWNLOAD, EventPayload(url=request.url))

    async def inspect(self, request: InspectRequest) -> ManifestInspectionResult:
        if not isinstance(request, InspectRequest):
            raise TypeError("request must be InspectRequest")
        async with self._operation_lock:
            self._ensure_open()
            return await self._inspection.execute(request)

    async def check_updates(self, request: UpdateCheckRequest) -> UpdateResult:
        if not isinstance(request, UpdateCheckRequest):
            raise TypeError("request must be UpdateCheckRequest")
        async with self._operation_lock:
            self._ensure_open()
            async with OperationDiagnosticsScope(
                self.logger,
                lambda: self.notifications.flush(source_url=request.url),
            ) as diagnostics:
                await self.events.emit(EventName.UPDATE_CHECK_STARTED, EventPayload(url=request.url))
                try:
                    result, _snapshot = await self._updates.execute(request, diagnostics)
                    return result
                finally:
                    await self.events.emit(EventName.UPDATE_CHECK_FINISHED, EventPayload(url=request.url))

    async def plan_workflow(self, request: WorkflowPlanRequest) -> WorkflowPlanResult:
        if not isinstance(request, WorkflowPlanRequest):
            raise TypeError("request must be WorkflowPlanRequest")
        async with self._operation_lock:
            self._ensure_open()
            return await self._plans.execute(request)

    async def workflow(self, request: WorkflowRequest) -> WorkflowResult:
        if not isinstance(request, WorkflowRequest):
            raise TypeError("request must be WorkflowRequest")
        async with self._operation_lock:
            self._ensure_open()
            return await self._workflows.execute(request)

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("DownloadService is closed")

    async def close(self) -> None:
        task = self._close_task
        if task is None:
            task = self._close_task = asyncio.create_task(self._close_runtime())
        elif task.done():
            return
        settled = await settle_task(task)
        error = settled.error if settled.error is not None else settled.value
        if error is not None:
            raise error
        if settled.cancellation is not None:
            raise settled.cancellation

    async def _close_runtime(self) -> BaseException | None:
        # Hold the operation lock until every resource, including the worker
        # thread, has finished. Cancelling a waiter never cancels this task.
        async with self._operation_lock:
            self._closed = True
            return await shutdown_resources(
                persist_cookies=self._persist_cookies if self._persist_cookies_on_close else None,
                gateway=self.gateway,
                image_processor=self.image_processor,
                registry=self.registry,
                logger=self.logger,
            )

    async def _persist_cookies(self) -> None:
        # Persistence can hold the cookie lock; closing the client must wait for it.
        await finish_transaction(
            partial(
                self.cookie_store.persist_delta,
                self._cookie_baseline,
                self.gateway.client.cookies.jar,
            )
        )
