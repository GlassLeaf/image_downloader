"""Download service and operation orchestration."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import TypeVar, cast
from urllib.parse import urlparse

from ..configuration.hosts import normalize_host, site_file_name
from ..configuration.models import AppConfig
from ..credentials.plugin_secrets import RuntimeSecrets
from ..exceptions import (
    AuthenticationError,
    ConfigurationError,
    ErrorInfo,
    ExistingFileConflictError,
    ImageDownloaderError,
    ImageProcessingError,
    InterProcessLockError,
    PluginError,
    RequestError,
    StorageError,
    StorageSafetyError,
    error_info_for,
)
from ..media.artifact_pipeline import ArtifactPipeline
from ..media.processor_chain import OperationProcessorChain
from ..models import (
    Chapter,
    ChapterResult,
    DownloadManifest,
    DownloadResult,
    FailureKind,
    ImageArtifact,
    ImageFailure,
    ImageOutcome,
    ImageOutcomeKind,
    ImageResource,
    RequestResponse,
    RequestSpec,
    UpdateChangeKind,
    UpdateResult,
)
from ..observability.chapter_reporter import ChapterReporter
from ..observability.diagnostic_safety import best_effort_diagnostic
from ..observability.events import EventName, EventPayload
from ..observability.scope import OperationDiagnosticsScope
from ..output.output_allocator import OutputAllocation, OutputAllocator
from ..plugins.lifecycle import PluginRecord
from ..plugins.plugin_invoker import PluginInvoker
from ..plugins.plugin_manifest import PluginConfigOverrides
from ..plugins.runtime import PluginRuntime, safe_app_settings
from ..ports import PluginExecutionContext, SitePlugin, UpdateProvider
from ..storage import FileSystem, safe_component
from ..transport.gateway import OperationRequestGateway
from .dependencies import _RuntimeDependencies

_ResultT = TypeVar("_ResultT")
_IMAGE_FAILURE_EVENTS = {
    FailureKind.FETCH: EventName.FETCH_FAILED,
    FailureKind.PROCESS: EventName.IMAGE_PROCESS_FAILED,
    FailureKind.SAVE: EventName.SAVE_FAILED,
}
_IMAGE_FAILURE_ACTIONS = {
    FailureKind.FETCH: "image_fetch",
    FailureKind.PROCESS: "image_process",
    FailureKind.SAVE: "image_save",
}
_IMAGE_FAILURE_STAGES = {
    FailureKind.FETCH: "image_fetch",
    FailureKind.PROCESS: "image_processing",
    FailureKind.SAVE: "image_save",
}


class _PluginRequests:
    """Capability-reduced request port exposed through PluginExecutionContext."""

    __slots__ = ("_gateway",)

    def __init__(self, gateway: OperationRequestGateway) -> None:
        self._gateway = gateway

    async def execute(self, spec: RequestSpec) -> RequestResponse:
        return await self._gateway.execute(spec)


class _ImageJobError(Exception):
    def __init__(self, kind: FailureKind, cause: Exception, response: RequestResponse | None = None) -> None:
        classified = _classify_image_error(kind, cause)
        super().__init__(str(classified))
        self.kind, self.cause, self.response = kind, classified, response


def _classify_image_error(kind: FailureKind, cause: Exception) -> ImageDownloaderError:
    if isinstance(cause, ImageDownloaderError):
        return cause
    error: ImageDownloaderError
    if kind is FailureKind.FETCH:
        error = RequestError("unexpected image fetch failure")
    elif kind is FailureKind.PROCESS:
        error = ImageProcessingError("unexpected image processing failure")
    else:
        error = StorageError("unexpected image save failure")
    error.__cause__ = cause
    return error


def _failure_code(error: Exception) -> str:
    return error_info_for(error).code


def _failure_output_path(error: Exception, filesystem: FileSystem) -> Path | None:
    """Return the absolute target path carried by an expected output conflict."""

    if isinstance(error, ExistingFileConflictError):
        return filesystem.path(error.relative_path)
    return None


def _failure_info(
    error: Exception,
    response: RequestResponse | None,
    filesystem: FileSystem,
) -> tuple[ErrorInfo, str | None, int | None, Path | None]:
    info = error_info_for(error)
    response_url = response.url if response is not None else info.response_url
    http_status = response.status if response is not None else info.http_status
    output_path = _failure_output_path(error, filesystem)
    return info, response_url, http_status, output_path


def _failure_transport(kind: FailureKind, error: Exception, response: RequestResponse | None) -> str:
    if response is not None or kind in {FailureKind.PROCESS, FailureKind.SAVE}:
        return "completed"
    code = _failure_code(error)
    return {
        "http_status_error": "response_received",
        "response_size_limit_error": "response_limit_exceeded",
        "redirect_policy_error": "redirect_rejected",
    }.get(code, "failed")


def _is_fatal_image_error(error: ImageDownloaderError, continue_on_image_error: bool) -> bool:
    return not continue_on_image_error or isinstance(
        error,
        (AuthenticationError, PluginError, ConfigurationError, StorageSafetyError, InterProcessLockError),
    )


class DownloadService:
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
        self.gateway = dependencies.gateway
        self.image_processor = dependencies.image_processor
        self.output_locks = dependencies.output_locks
        self._operation_lock = asyncio.Lock()
        self._closed = False

    async def __aenter__(self) -> DownloadService:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def run(
        self,
        url: str,
        *,
        plugin_overrides: PluginConfigOverrides | None = None,
        fallback_override: bool | None = None,
    ) -> DownloadResult:
        async with self._operation_lock:
            self._ensure_open()
            async with OperationDiagnosticsScope(
                self.logger,
                lambda: self.notifications.flush(source_url=url),
            ) as diagnostics:
                await self.events.emit(EventName.BEFORE_DOWNLOAD, EventPayload(url=url))
                try:
                    return await self._run_operation(
                        url,
                        plugin_overrides,
                        fallback_override,
                        diagnostics,
                    )
                finally:
                    await self.events.emit(EventName.AFTER_DOWNLOAD, EventPayload(url=url))

    async def _run_operation(
        self,
        url: str,
        plugin_overrides: PluginConfigOverrides | None,
        fallback_override: bool | None,
        diagnostics: OperationDiagnosticsScope,
    ) -> DownloadResult:
        try:
            record, plugin, context, operation_gateway = self._operation(
                url,
                plugin_overrides,
                fallback_override,
            )
            invoker = PluginInvoker(record.id, self.logger)
            diagnostics.capture(self._python_log_namespaces(record, include_processors=True))
            async with OperationProcessorChain(
                self.registry,
                self.config.image_processors.chain,
                plugin_overrides,
                self.logger,
            ) as processors:
                await best_effort_diagnostic(
                    self.logger.core, "operation_started", module="runtime", url=url, plugin_id=record.id, debug=True
                )
                await best_effort_diagnostic(
                    self.logger.core, "plugin_selected", module="plugin", url=url, plugin_id=record.id, debug=True
                )
                manifest = await invoker.inspect(plugin, url, context)
                manifest = replace(manifest, metadata={**manifest.metadata, "source_url": url})
                if not manifest.chapters:
                    if not self.config.download.allow_empty_chapter_manifest:
                        raise PluginError("plugin returned an empty manifest")
                    await self._empty_reporter(manifest, record, url)
                    result = DownloadResult(url, manifest, ())
                else:
                    allocator = OutputAllocator(self._output_filesystem(record, url), self.config)
                    pipeline = ArtifactPipeline(
                        self.config,
                        self.registry,
                        record,
                        plugin_overrides,
                        self.image_processor,
                        self.logger,
                        invoker,
                        processors.bindings,
                    )
                    results = await self._run_chapters(
                        plugin,
                        context,
                        manifest,
                        allocator,
                        pipeline,
                        operation_gateway,
                        record.id,
                    )
                    result = DownloadResult(url, manifest, tuple(results))
            outcome = (
                EventName.DOWNLOAD_SUCCESS
                if not result.failures
                else EventName.DOWNLOAD_PARTIAL_SUCCESS
                if result.saved_files or result.skipped_files
                else EventName.DOWNLOAD_FAILED
            )
            await self.events.emit(
                outcome,
                EventPayload(url=url),
            )
            await self.events.emit(EventName.DOWNLOAD_COMPLETE, EventPayload(url=url))
            await best_effort_diagnostic(self.logger.core, "download_finished", module="download", url=url, debug=True)
            return result
        except asyncio.CancelledError:
            await best_effort_diagnostic(self.logger.core, "download_cancelled", module="runtime", url=url, debug=True)
            raise
        except Exception as exc:
            await self._record_operation_failure(url, exc)
            raise

    async def check_updates(
        self,
        url: str,
        *,
        plugin_overrides: PluginConfigOverrides | None = None,
        fallback_override: bool | None = None,
    ) -> UpdateResult:
        async with self._operation_lock:
            self._ensure_open()
            async with OperationDiagnosticsScope(
                self.logger,
                lambda: self.notifications.flush(source_url=url),
            ) as diagnostics:
                await self.events.emit(EventName.UPDATE_CHECK_STARTED, EventPayload(url=url))
                try:
                    return await self._check_updates_operation(
                        url,
                        plugin_overrides,
                        fallback_override,
                        diagnostics,
                    )
                finally:
                    await self.events.emit(EventName.UPDATE_CHECK_FINISHED, EventPayload(url=url))

    async def _check_updates_operation(
        self,
        url: str,
        plugin_overrides: PluginConfigOverrides | None,
        fallback_override: bool | None,
        diagnostics: OperationDiagnosticsScope,
    ) -> UpdateResult:
        try:
            record, plugin, context, _ = self._operation(url, plugin_overrides, fallback_override)
            invoker = PluginInvoker(record.id, self.logger)
            diagnostics.capture(self._python_log_namespaces(record, include_processors=False))
            await best_effort_diagnostic(
                self.logger.core,
                "operation_started",
                module="runtime",
                url=url,
                plugin_id=record.id,
                action="check_updates",
                debug=True,
            )
            await best_effort_diagnostic(
                self.logger.core,
                "plugin_selected",
                module="plugin",
                url=url,
                plugin_id=record.id,
                action="check_updates",
                debug=True,
            )
            if not isinstance(plugin, UpdateProvider):
                raise PluginError("update check is not supported by this plugin")
            snapshot = await invoker.check_updates(plugin, url, context)
            changes = await self.state.apply_snapshot_async(record.id, url, snapshot)
            for change in changes:
                if change.kind in {UpdateChangeKind.ADDED, UpdateChangeKind.CHANGED}:
                    await self.events.emit(EventName.UPDATED_URL_FOUND, EventPayload(url=change.url))
            await best_effort_diagnostic(
                self.logger.core,
                "response_received",
                module="download",
                url=url,
                count=len(changes),
                plugin_id=record.id,
                debug=True,
            )
            return UpdateResult(url, record.id, changes, snapshot.checked_at)
        except asyncio.CancelledError:
            await best_effort_diagnostic(
                self.logger.core, "download_cancelled", module="runtime", url=url, action="check_updates", debug=True
            )
            raise
        except Exception as exc:
            info = error_info_for(exc)
            await self.events.emit(
                EventName.UPDATE_FAILED,
                EventPayload(
                    url=url,
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
                "operation_failed", module="runtime", url=url, error=exc, action="check_updates", debug=True
            )
            await best_effort_diagnostic(self.logger.error_detail, exc, url=url, module="runtime")
            raise

    def _operation(
        self,
        url: str,
        overrides: PluginConfigOverrides | None = None,
        fallback_override: bool | None = None,
    ) -> tuple[PluginRecord, SitePlugin, PluginExecutionContext, OperationRequestGateway]:
        fallback_enabled = self.config.fallback.generic_html.enabled if fallback_override is None else fallback_override
        record, plugin = self.registry.resolve(url, fallback_enabled=fallback_enabled, overrides=overrides)
        self.registry.validate_operation_overrides(record, overrides)
        settings = self.config.plugin_settings.get(record.id)
        secrets = RuntimeSecrets(record.id, settings.secrets if settings else {})
        invoker = PluginInvoker(record.id, self.logger)
        context: PluginExecutionContext | None = None

        def auth_flow_factory(session: OperationRequestGateway) -> object | None:
            nonlocal context
            context = PluginExecutionContext(
                self.registry.effective_config(record, overrides),
                safe_app_settings(self.config),
                record.manifest.value,
                record.catalog.as_json() if record.catalog else None,
                secrets,
                _PluginRequests(session),
            )
            return invoker.auth_flow(plugin, context)

        operation_gateway = self.gateway.operation(
            plugin_id=record.id,
            operation_url=url,
            auth_flow_factory=auth_flow_factory,
            invoker=invoker,
        )
        if context is None:
            raise RuntimeError("operation context was not initialized")
        return record, plugin, context, operation_gateway

    def _python_log_namespaces(self, site_record: PluginRecord, *, include_processors: bool) -> tuple[str, ...]:
        records = [site_record]
        if include_processors:
            records.extend(
                record
                for plugin_id in self.config.image_processors.chain
                if (record := self.registry.records.get(plugin_id)) is not None and self.registry.enabled(record)
            )
        namespaces = (self.registry.module_namespace(record) for record in records)
        return tuple(namespace for namespace in namespaces if namespace is not None)

    def _output_filesystem(self, record: PluginRecord, url: str) -> FileSystem:
        """Return the operation output root, optionally namespaced by source host/plugin."""
        if not self.config.output.isolate_by_plugin:
            return self.outputs
        parsed = urlparse(url)
        if not parsed.hostname:
            raise ConfigurationError("download URL must include a host")
        host, _ = normalize_host(parsed.hostname)
        # ``site_file_name`` is already the safe canonical spelling used by the
        # configuration tree; omit only the YAML suffix for output directories.
        host_component = site_file_name(host).removesuffix(".yaml")
        relative = Path(host_component) / safe_component(record.id, max_length=self.config.output.max_component_length)
        self.outputs.ensure_directory(relative)
        return FileSystem(self.outputs.path(relative))

    async def _record_operation_failure(self, url: str, error: Exception) -> None:
        info = error_info_for(error)
        event = (
            EventName.AUTH_FAILED
            if isinstance(error, AuthenticationError)
            else EventName.PLUGIN_FAILED
            if isinstance(error, PluginError)
            else EventName.CONFIG_FAILED
            if isinstance(error, ConfigurationError)
            else EventName.STORAGE_FAILED
            if isinstance(error, StorageError)
            else EventName.RUNTIME_FAILED
        )
        payload = EventPayload(
            url=url,
            response_url=info.response_url,
            path=info.output_path,
            http_status=info.http_status,
            error_code=info.code,
            error_reason=info.reason,
            error_class=info.exception,
            operation="download",
        )
        if not getattr(error, "_image_failure_reported", False):
            await self.events.emit(event, payload)
        await self.events.emit(EventName.DOWNLOAD_FAILED, payload)
        await best_effort_diagnostic(
            self.logger.core, "operation_failed", module="runtime", url=url, error=error, debug=True
        )
        await best_effort_diagnostic(self.logger.error_detail, error, url=url, module="runtime")

    async def _bounded(
        self,
        factories: Sequence[Callable[[], Awaitable[_ResultT]]],
        limit: int,
    ) -> list[_ResultT]:
        """Run at most ``limit`` jobs, never launching queued jobs after a failure."""
        results: list[_ResultT | None] = [None] * len(factories)
        pending = iter(enumerate(factories))
        active: dict[asyncio.Future[_ResultT], int] = {}

        def start_next() -> bool:
            try:
                index, factory = next(pending)
            except StopIteration:
                return False
            active[asyncio.ensure_future(factory())] = index
            return True

        for _ in range(min(limit, len(factories))):
            start_next()
        try:
            while active:
                done, _ = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
                error: BaseException | None = None
                completed = 0
                for task in done:
                    index = active.pop(task)
                    try:
                        results[index] = task.result()
                        completed += 1
                    except BaseException as exc:
                        error = exc
                if error is not None:
                    # Started work is collected; queued work is intentionally never created.
                    await asyncio.gather(*active, return_exceptions=True)
                    active.clear()
                    raise error
                for _ in range(completed):
                    start_next()
        except asyncio.CancelledError:
            for task in active:
                task.cancel()
            await asyncio.gather(*active, return_exceptions=True)
            raise
        return [cast(_ResultT, result) for result in results]

    async def _run_chapters(
        self,
        plugin: SitePlugin,
        context: PluginExecutionContext,
        manifest: DownloadManifest,
        allocator: OutputAllocator,
        pipeline: ArtifactPipeline,
        operation_gateway: OperationRequestGateway,
        plugin_id: str,
    ) -> list[ChapterResult]:
        operation_id = uuid.uuid4().hex

        async def run_one(position: int, chapter: Chapter) -> ChapterResult:
            return await self._run_chapter(
                plugin,
                context,
                manifest,
                chapter,
                allocator,
                pipeline,
                operation_gateway,
                plugin_id,
                f"{operation_id}-chapter-{position}",
            )

        factories = [
            lambda position=position, chapter=chapter: run_one(position, chapter)
            for position, chapter in enumerate(manifest.chapters)
        ]
        try:
            return cast(
                list[ChapterResult],
                await self._bounded(factories, self.config.download.chapter_concurrency),
            )
        finally:
            await asyncio.gather(
                *(
                    best_effort_diagnostic(self.logger.close_chapter, f"{operation_id}-chapter-{position}")
                    for position in range(len(factories))
                )
            )

    async def _run_chapter(
        self,
        plugin: SitePlugin,
        context: PluginExecutionContext,
        manifest: DownloadManifest,
        chapter: Chapter,
        allocator: OutputAllocator,
        pipeline: ArtifactPipeline,
        operation_gateway: OperationRequestGateway,
        plugin_id: str,
        reporter_id: str,
    ) -> ChapterResult:
        directory = allocator.chapter_directory(chapter)
        reporter = ChapterReporter(allocator.filesystem, directory, manifest, chapter, self.logger, reporter_id)
        await reporter.start()
        chapter_id = str(chapter.number)
        await best_effort_diagnostic(
            self.logger.core,
            "chapter_started",
            module="download",
            chapter_id=chapter_id,
            url=manifest.metadata.get("source_url"),
            count=len(chapter.images),
            plugin_id=plugin_id,
            debug=True,
        )

        async def run_one(position: int, image: ImageResource) -> ImageOutcome:
            response: RequestResponse | None = None
            try:
                try:
                    await self.events.emit(EventName.BEFORE_FETCH, EventPayload(url=image.url))
                    await best_effort_diagnostic(
                        self.logger.core,
                        "request_started",
                        module="download",
                        chapter_id=chapter_id,
                        url=image.url,
                        count=image.index,
                        plugin_id=plugin_id,
                        action="image_fetch",
                        debug=True,
                    )
                    response = await operation_gateway.execute_image(plugin, image, context)
                    await best_effort_diagnostic(
                        self.logger.core,
                        "download_finished",
                        module="download",
                        chapter_id=chapter_id,
                        url=response.url,
                        bytes_count=len(response.body),
                        count=image.index,
                        plugin_id=plugin_id,
                        action="image_fetch",
                        debug=True,
                    )
                    await self.events.emit(EventName.FETCH_SUCCESS, EventPayload(url=image.url))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    raise _ImageJobError(FailureKind.FETCH, exc) from exc
                assert response is not None
                artifact = ImageArtifact(
                    response.body, response.headers.get("content-type", ""), image.url, image.image_id
                )
                try:
                    await self.events.emit(EventName.IMAGE_PROCESS_STARTED, EventPayload(url=image.url))
                    processed = await pipeline.process(plugin, artifact, image, manifest, chapter)
                    await self.events.emit(EventName.IMAGE_PROCESS_SUCCESS, EventPayload(url=image.url))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    raise _ImageJobError(FailureKind.PROCESS, exc, response) from exc
                try:
                    await self.events.emit(EventName.BEFORE_SAVE, EventPayload(url=image.url))
                    async with self.output_locks.hold(allocator.filesystem.path(directory)):
                        await allocator.refresh_directory(directory)
                        allocation = await allocator.allocate(directory, image, chapter, processed.extension or ".jpeg")
                        path = allocator.filesystem.path(allocation.relative_path)
                        if allocation.should_write:
                            try:
                                path = allocator.filesystem.write_bytes_atomic(allocation.relative_path, processed.data)
                            except BaseException:
                                await self._settle_allocation(allocation, success=False)
                                raise
                            await self._settle_allocation(allocation, success=True)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    raise _ImageJobError(FailureKind.SAVE, exc, response) from exc
                if allocation.should_write:
                    await self.events.emit(EventName.SAVE_SUCCESS, EventPayload(url=image.url, path=str(path)))
                    await best_effort_diagnostic(
                        self.logger.core,
                        "file_saved",
                        module="storage",
                        chapter_id=chapter_id,
                        url=image.url,
                        path=path,
                        bytes_count=len(processed.data),
                        count=image.index,
                        plugin_id=plugin_id,
                        debug=True,
                    )
                if not allocation.should_write:
                    outcome = ImageOutcome(
                        image,
                        ImageOutcomeKind.SKIPPED,
                        str(path),
                    )
                else:
                    outcome = ImageOutcome(image, ImageOutcomeKind.SAVED, str(path))
            except asyncio.CancelledError:
                raise
            except _ImageJobError as exc:
                info, response_url, http_status, failure_path = _failure_info(
                    exc.cause,
                    exc.response,
                    allocator.filesystem,
                )
                transport = _failure_transport(exc.kind, exc.cause, exc.response)
                await self.events.emit(
                    _IMAGE_FAILURE_EVENTS[exc.kind],
                    EventPayload(
                        url=image.url,
                        response_url=response_url,
                        path=str(failure_path) if failure_path is not None else None,
                        http_status=http_status,
                        stage=_IMAGE_FAILURE_STAGES[exc.kind],
                        chapter_id=chapter_id,
                        image_index=image.index,
                        error_code=info.code,
                        error_reason=info.reason,
                        error_class=info.exception,
                        operation="download",
                        transport=transport,
                    ),
                )
                await best_effort_diagnostic(
                    self.logger.core,
                    "download_failed",
                    module="download",
                    chapter_id=chapter_id,
                    url=image.url,
                    count=image.index,
                    error=exc.cause,
                    plugin_id=plugin_id,
                    action=_IMAGE_FAILURE_ACTIONS[exc.kind],
                    debug=True,
                )
                await best_effort_diagnostic(
                    self.logger.error_detail,
                    exc.cause,
                    chapter_id=chapter_id,
                    url=image.url,
                    path=failure_path,
                    module="image",
                )
                outcome = ImageOutcome(
                    image,
                    ImageOutcomeKind.FAILED,
                    path=str(failure_path) if failure_path is not None else None,
                    failure=ImageFailure(
                        exc.kind,
                        info.exception,
                        info.message,
                        code=info.code,
                        reason=info.reason,
                        response_url=response_url,
                        http_status=http_status,
                        output_path=info.output_path,
                        transport=transport,
                    ),
                )
                await reporter.record(position, outcome)
                if _is_fatal_image_error(exc.cause, self.config.download.continue_on_image_error):
                    exc.cause._image_failure_reported = True
                    exc.cause._image_failure_context = {
                        "stage": _IMAGE_FAILURE_STAGES[exc.kind],
                        "transport": transport,
                        "image_url": image.url,
                        "response_url": response_url,
                        "http_status": http_status,
                        "output_path": info.output_path,
                    }
                    raise exc.cause from exc.cause.__cause__
                return outcome
            await reporter.record(position, outcome)
            return outcome

        factories = [
            lambda position=position, image=image: run_one(position, image)
            for position, image in enumerate(chapter.images)
        ]
        try:
            bounded = await self._bounded(factories, self.config.download.image_concurrency_per_chapter)
            outcomes = tuple(bounded)
        finally:
            await reporter.finish()
        await best_effort_diagnostic(
            self.logger.core,
            "chapter_finished",
            module="download",
            chapter_id=chapter_id,
            url=manifest.metadata.get("source_url"),
            count=len(outcomes),
            plugin_id=plugin_id,
            debug=True,
        )
        return ChapterResult(chapter, outcomes)

    @staticmethod
    async def _settle_allocation(allocation: OutputAllocation, *, success: bool) -> None:
        task = asyncio.create_task(allocation.commit() if success else allocation.abort())
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # The OS lock must not be released while the reservation is unfinished.
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
            task.result()
            raise

    async def _empty_reporter(self, manifest: DownloadManifest, record: PluginRecord, url: str) -> None:
        chapter = Chapter(0, manifest.title)
        outputs = self._output_filesystem(record, url)
        directory = OutputAllocator(outputs, self.config).chapter_directory(chapter)
        reporter_id = f"{uuid.uuid4().hex}-empty-manifest"
        reporter = ChapterReporter(outputs, directory, manifest, chapter, self.logger, reporter_id)
        try:
            await reporter.start()
            await reporter.finish()
        finally:
            await best_effort_diagnostic(self.logger.close_chapter, reporter_id)

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("DownloadService is closed")

    async def close(self) -> None:
        async with self._operation_lock:
            if not self._closed:
                self._closed = True
                try:
                    await self._persist_cookies()
                finally:
                    try:
                        await self.gateway.close()
                    finally:
                        try:
                            await asyncio.to_thread(self.image_processor.close)
                        finally:
                            try:
                                self.registry.close()
                            finally:
                                await best_effort_diagnostic(self.logger.close)

    async def _persist_cookies(self) -> None:
        task = asyncio.create_task(
            asyncio.to_thread(self.cookie_store.persist_delta, self._cookie_baseline, self.gateway.client.cookies.jar)
        )
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # The thread may already hold the file lock. Finish the transaction
            # before closing the client and propagating cancellation.
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
            task.result()
            raise
