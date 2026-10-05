"""Download service and operation orchestration."""

from __future__ import annotations

import asyncio
import math
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import Any, Literal, NoReturn, TypeVar, cast
from urllib.parse import urlparse

from ..configuration.hosts import normalize_host, site_file_name
from ..configuration.models import AppConfig, ImageFormat, PluginDownloadPolicy
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
    UpdateCheckUnsupportedError,
    error_info_for,
)
from ..media.artifact_pipeline import ArtifactPipeline
from ..media.processor_chain import OperationProcessorChain
from ..models import (
    AdditionalFileHookPoint,
    Chapter,
    ChapterResult,
    DownloadManifest,
    DownloadResult,
    FailureKind,
    ImageArtifact,
    ImageFailure,
    ImageFetchRequest,
    ImageOutcome,
    ImageOutcomeKind,
    ImageRequestResolution,
    ImageRequestResolutionFailure,
    ImageRequestResolutionStatus,
    ImageResource,
    ManifestInspectionResult,
    RequestResponse,
    RequestSpec,
    UpdateChangeKind,
    UpdateResult,
    UpdateSnapshot,
    WorkflowPlanResult,
    WorkflowResult,
)
from ..observability.chapter_reporter import ChapterReporter
from ..observability.diagnostic_safety import best_effort_diagnostic
from ..observability.events import EventName, EventPayload
from ..observability.logging import DownloadLogger
from ..observability.scope import OperationDiagnosticsScope
from ..output.original_filename import resolve_original_filename
from ..output.output_allocator import OutputAllocation, OutputAllocator, OutputFormatContext
from ..plugins.format_values import PluginFormatValueParticipant, collect_plugin_format_values
from ..plugins.lifecycle import PluginRecord
from ..plugins.plugin_invoker import PluginInvoker
from ..plugins.plugin_manifest import PluginConfigOverrides, PluginDownloadPolicyOverrides
from ..plugins.runtime import PluginRuntime, safe_app_settings
from ..ports import PluginExecutionContext, SitePlugin, UpdateProvider
from ..storage import FileSystem, safe_component
from ..storage._path_limits import _component_limit
from ..transport.gateway import OperationRequestGateway, RequestGateway
from .additional_files import AdditionalFiles
from .dependencies import _RuntimeDependencies
from .workflow_retry import ImageLedger

_ResultT = TypeVar("_ResultT")


async def _additional_image(
    additional: AdditionalFiles | None,
    point: AdditionalFileHookPoint,
    manifest: DownloadManifest,
    chapter: Chapter,
    image: ImageResource,
    **values: Any,
) -> None:
    if additional is not None:
        await additional.run(point, manifest=manifest, chapter=chapter, image=image, **values)


def _remember_image(ledger: ImageLedger | None, cp: int, ip: int, outcome: ImageOutcome) -> None:
    if ledger is not None:
        ledger.record(cp, ip, outcome)


def _start_workflow_image(ledger: ImageLedger | None, cp: int, ip: int) -> None:
    if ledger is not None:
        ledger.attempted.add((cp, ip))


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


@dataclass(frozen=True, slots=True)
class _OperationDownloadPolicy:
    request_concurrency: int
    chapter_concurrency: int
    image_concurrency_per_chapter: int


def _effective_download_policy(config: AppConfig, policy: PluginDownloadPolicy) -> _OperationDownloadPolicy:
    request_concurrency = min(
        config.network.request_concurrency,
        policy.request_concurrency or config.network.request_concurrency,
    )
    chapter_concurrency = min(
        config.download.chapter_concurrency,
        policy.chapter_concurrency or config.download.chapter_concurrency,
    )
    image_concurrency = min(
        config.download.image_concurrency_per_chapter,
        policy.image_concurrency_per_chapter or config.download.image_concurrency_per_chapter,
    )
    if policy.preserve_image_start_order:
        chapter_concurrency = image_concurrency = 1
    return _OperationDownloadPolicy(request_concurrency, chapter_concurrency, image_concurrency)


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
    error.__dict__["_unexpected_image_error"] = not (kind is FailureKind.SAVE and isinstance(cause, OSError))
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


def _workflow_image_error(error: ImageDownloaderError, ledger: ImageLedger | None) -> Exception:
    if (
        ledger is not None
        and getattr(error, "_unexpected_image_error", False)
        and isinstance(error.__cause__, Exception)
    ):
        return error.__cause__
    return error


def _raise_image_error(error: ImageDownloaderError, ledger: ImageLedger | None) -> NoReturn:
    failure = _workflow_image_error(error, ledger)
    if failure is error:
        raise error from error.__cause__
    failure.__dict__["_image_failure_reported"] = True
    failure.__dict__["_image_failure_context"] = error._image_failure_context
    raise failure from None


def _is_fatal_image_error(
    error: ImageDownloaderError,
    continue_on_image_error: bool,
    ledger: ImageLedger | None = None,
) -> bool:
    return (
        (ledger is not None and getattr(error, "_unexpected_image_error", False))
        or not continue_on_image_error
        or isinstance(
            error,
            (AuthenticationError, PluginError, ConfigurationError, StorageSafetyError, InterProcessLockError),
        )
    )


def _parallel_failure_priority(error: BaseException) -> int:
    """Preserve cancellation and operation-stopping failures over URL failures."""
    if not isinstance(error, Exception):
        return 0
    if isinstance(
        error,
        (AuthenticationError, PluginError, RequestError, ImageProcessingError, ExistingFileConflictError),
    ):
        return 2
    return 1


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
        self._persist_cookies_on_close = dependencies.persist_cookies_on_close
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
        plugin_id: str | None = None,
        force_plugin: bool = False,
        plugin_download_policy_overrides: PluginDownloadPolicyOverrides | None = None,
        force_image_format: ImageFormat | None = None,
    ) -> DownloadResult:
        if force_image_format not in (None, "ORIGINAL", "JPEG", "PNG", "WEBP"):
            raise ValueError("force_image_format must be ORIGINAL, JPEG, PNG, WEBP, or None")
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
                        plugin_id,
                        force_plugin,
                        plugin_download_policy_overrides,
                        force_image_format,
                        diagnostics,
                    )
                finally:
                    await self.events.emit(EventName.AFTER_DOWNLOAD, EventPayload(url=url))

    async def inspect(
        self,
        url: str,
        *,
        plugin_overrides: PluginConfigOverrides | None = None,
        fallback_override: bool | None = None,
        plugin_id: str | None = None,
        force_plugin: bool = False,
        resolve_image_requests: bool = True,
    ) -> ManifestInspectionResult:
        """Resolve a manifest and image request specs without fetching image bodies.

        The operation uses a detached gateway and cookie jar, so request-time
        session changes and diagnostic records are discarded when it finishes.
        ``create_image_request`` may still perform plugin-defined auxiliary HTTP.
        """
        if not isinstance(resolve_image_requests, bool):
            raise TypeError("resolve_image_requests must be bool")
        async with self._operation_lock:
            self._ensure_open()
            temporary_gateway = RequestGateway(
                self.config,
                self.cookie_store.clone_jar(self.gateway.client.cookies.jar),
            )
            try:
                async with self._site_operation(
                    url,
                    plugin_overrides,
                    fallback_override,
                    plugin_id,
                    force_plugin,
                    request_gateway=temporary_gateway,
                    diagnostics_enabled=False,
                ) as (record, plugin, context, operation_gateway, _, invoker):
                    manifest = self._normalized_manifest(await invoker.inspect(plugin, url, context), url)
                    requests: list[ImageRequestResolution] = []
                    if resolve_image_requests:
                        for chapter_position, chapter in enumerate(manifest.chapters, start=1):
                            for image_position, image in enumerate(chapter.images, start=1):
                                try:
                                    value = await invoker.create_image_request(plugin, image, context)
                                except asyncio.CancelledError:
                                    raise
                                except Exception as exc:
                                    info = error_info_for(exc)
                                    requests.append(
                                        ImageRequestResolution(
                                            chapter_position,
                                            image_position,
                                            ImageRequestResolutionStatus.FAILED,
                                            failure=ImageRequestResolutionFailure(
                                                info.code,
                                                info.reason,
                                                info.exception,
                                                "create_image_request",
                                            ),
                                        )
                                    )
                                else:
                                    request = value.request if isinstance(value, ImageFetchRequest) else value
                                    plugin_data = value.plugin_data if isinstance(value, ImageFetchRequest) else {}
                                    try:
                                        effective_request = await operation_gateway.preview(request)
                                    except asyncio.CancelledError:
                                        raise
                                    except Exception as exc:
                                        info = error_info_for(exc)
                                        requests.append(
                                            ImageRequestResolution(
                                                chapter_position,
                                                image_position,
                                                ImageRequestResolutionStatus.PARTIALLY_RESOLVED,
                                                request,
                                                plugin_data,
                                                isinstance(value, ImageFetchRequest),
                                                ImageRequestResolutionFailure(
                                                    info.code,
                                                    info.reason,
                                                    info.exception,
                                                    "effective_request",
                                                ),
                                            )
                                        )
                                    else:
                                        requests.append(
                                            ImageRequestResolution(
                                                chapter_position,
                                                image_position,
                                                ImageRequestResolutionStatus.RESOLVED,
                                                request,
                                                plugin_data,
                                                isinstance(value, ImageFetchRequest),
                                                effective_request=effective_request,
                                            )
                                        )
                    return ManifestInspectionResult(
                        url,
                        record.id,
                        manifest,
                        resolve_image_requests,
                        tuple(requests),
                    )
            finally:
                await temporary_gateway.close()

    async def _run_operation(
        self,
        url: str,
        plugin_overrides: PluginConfigOverrides | None,
        fallback_override: bool | None,
        plugin_id: str | None,
        force_plugin: bool,
        plugin_download_policy_overrides: PluginDownloadPolicyOverrides | None,
        force_image_format: ImageFormat | None,
        diagnostics: OperationDiagnosticsScope,
        ledger: ImageLedger | None = None,
    ) -> DownloadResult:
        try:
            async with self._site_operation(
                url,
                plugin_overrides,
                fallback_override,
                plugin_id,
                force_plugin,
                plugin_download_policy_overrides,
            ) as (record, plugin, context, operation_gateway, policy, invoker):
                diagnostics.capture(self._python_log_namespaces(record, include_processors=True))
                async with OperationProcessorChain(
                    self.registry,
                    self.config.image_processors.chain,
                    plugin_overrides,
                    self.logger,
                ) as processors:
                    await best_effort_diagnostic(
                        self.logger.core,
                        "operation_started",
                        module="runtime",
                        url=url,
                        plugin_id=record.id,
                        debug=True,
                    )
                    await best_effort_diagnostic(
                        self.logger.core, "plugin_selected", module="plugin", url=url, plugin_id=record.id, debug=True
                    )
                    plugin_values = self._collect_output_format_values(
                        url,
                        record,
                        plugin,
                        plugin_overrides,
                        invoker,
                        processors,
                    )
                    allocator = OutputAllocator(self._output_filesystem(record, url), self.config)
                    additional = AdditionalFiles(
                        self, plugin, context, allocator, record.id, url, plugin_values, ledger
                    )
                    try:
                        result = await self._run_with_additional(
                            additional,
                            plugin,
                            context,
                            invoker,
                            url,
                            ledger,
                            record,
                            plugin_values,
                            plugin_overrides,
                            processors,
                            force_image_format,
                            operation_gateway,
                            policy,
                        )
                    finally:
                        try:
                            additional.close()
                        except Exception as cleanup_error:
                            await best_effort_diagnostic(
                                self.logger.core,
                                "additional_file_cleanup_failed",
                                module="plugin",
                                error=cleanup_error,
                                debug=True,
                            )
            outcome = (
                EventName.DOWNLOAD_SUCCESS
                if not result.failures
                else EventName.DOWNLOAD_PARTIAL_SUCCESS
                if result.saved_files or result.skipped_files
                else EventName.DOWNLOAD_FAILED
            )
            await self.events.emit(outcome, EventPayload(url=url))
            await self.events.emit(EventName.DOWNLOAD_COMPLETE, EventPayload(url=url))
            await best_effort_diagnostic(self.logger.core, "download_finished", module="download", url=url, debug=True)
            return result
        except asyncio.CancelledError:
            await best_effort_diagnostic(self.logger.core, "download_cancelled", module="runtime", url=url, debug=True)
            raise
        except Exception as exc:
            await self._record_operation_failure(url, exc)
            raise

    async def _run_with_additional(
        self,
        additional: AdditionalFiles,
        plugin: SitePlugin,
        context: PluginExecutionContext,
        invoker: PluginInvoker,
        url: str,
        ledger: ImageLedger | None,
        record: PluginRecord,
        plugin_values: Mapping[str, Mapping[str, str]],
        plugin_overrides: PluginConfigOverrides | None,
        processors: OperationProcessorChain,
        force_image_format: ImageFormat | None,
        operation_gateway: OperationRequestGateway,
        policy: _OperationDownloadPolicy,
    ) -> DownloadResult:
        await additional.initialize()
        await additional.run(AdditionalFileHookPoint.BEFORE_MANIFEST)
        manifest = self._normalized_manifest(await invoker.inspect(plugin, url, context), url)
        if ledger is not None:
            ledger.begin(manifest, record.id)
        await additional.flush_pending(manifest)
        for chapter in manifest.chapters:
            await additional.run(AdditionalFileHookPoint.AFTER_MANIFEST, manifest=manifest, chapter=chapter)
        if not manifest.chapters:
            if not self.config.download.allow_empty_chapter_manifest:
                raise PluginError("plugin returned an empty manifest")
            await self._empty_reporter(manifest, record, url, plugin_values)
            result = DownloadResult(url, manifest, ())
        else:
            allocator = additional.allocator
            pipeline = ArtifactPipeline(
                self.config,
                self.registry,
                record,
                plugin_overrides,
                self.image_processor,
                self.logger,
                invoker,
                processors.bindings,
                force_image_format=force_image_format,
            )
            results = await self._run_chapters(
                plugin,
                context,
                manifest,
                allocator,
                pipeline,
                operation_gateway,
                record.id,
                policy,
                url,
                plugin_values,
                ledger,
                additional,
            )
            result = DownloadResult(url, manifest, tuple(results))
        for chapter_result in result.chapters:
            await additional.run(
                AdditionalFileHookPoint.AFTER_DOWNLOAD,
                manifest=manifest,
                chapter=chapter_result.chapter,
                chapter_result=chapter_result,
            )
        return replace(result, additional_files=tuple(ledger.additional_files if ledger else additional.outcomes))

    def _collect_output_format_values(
        self,
        url: str,
        record: PluginRecord,
        plugin: SitePlugin,
        overrides: PluginConfigOverrides | None,
        invoker: PluginInvoker,
        processors: OperationProcessorChain,
    ) -> Mapping[str, Mapping[str, str]]:
        app_settings = safe_app_settings(self.config)
        participants: list[PluginFormatValueParticipant] = [
            PluginFormatValueParticipant(
                record,
                plugin,
                self.registry.effective_config(record, overrides),
                app_settings,
                invoker,
            )
        ]
        participants.extend(
            PluginFormatValueParticipant(
                binding.record,
                binding.instance,
                binding.config,
                binding.app_settings,
                PluginInvoker(binding.plugin_id, self.logger),
            )
            for binding in processors.bindings
            if not isinstance(binding, str)
        )
        return collect_plugin_format_values(url, participants)

    async def check_updates(
        self,
        url: str,
        *,
        plugin_overrides: PluginConfigOverrides | None = None,
        fallback_override: bool | None = None,
        plugin_id: str | None = None,
        force_plugin: bool = False,
        plugin_download_policy_overrides: PluginDownloadPolicyOverrides | None = None,
    ) -> UpdateResult:
        async with self._operation_lock:
            self._ensure_open()
            async with OperationDiagnosticsScope(
                self.logger,
                lambda: self.notifications.flush(source_url=url),
            ) as diagnostics:
                await self.events.emit(EventName.UPDATE_CHECK_STARTED, EventPayload(url=url))
                try:
                    result, _snapshot = await self._check_updates_operation(
                        url,
                        plugin_overrides,
                        fallback_override,
                        plugin_id,
                        force_plugin,
                        plugin_download_policy_overrides,
                        diagnostics,
                    )
                    return result
                finally:
                    await self.events.emit(EventName.UPDATE_CHECK_FINISHED, EventPayload(url=url))

    async def _check_updates_operation(
        self,
        url: str,
        plugin_overrides: PluginConfigOverrides | None,
        fallback_override: bool | None,
        plugin_id: str | None,
        force_plugin: bool,
        plugin_download_policy_overrides: PluginDownloadPolicyOverrides | None,
        diagnostics: OperationDiagnosticsScope,
        selected_plugin: tuple[PluginRecord, SitePlugin] | None = None,
    ) -> tuple[UpdateResult, UpdateSnapshot]:
        try:
            async with self._site_operation(
                url,
                plugin_overrides,
                fallback_override,
                plugin_id,
                force_plugin,
                plugin_download_policy_overrides,
                selected_plugin=selected_plugin,
            ) as (record, plugin, context, _, _, invoker):
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
                snapshot = await self._fetch_update_snapshot(plugin, url, context, invoker)
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
                return UpdateResult(url, record.id, changes, snapshot.checked_at), snapshot
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
                "operation_failed",
                module="runtime",
                url=url,
                error=exc,
                action="check_updates",
                debug=True,
            )
            await best_effort_diagnostic(self.logger.error_detail, exc, url=url, module="runtime")
            raise

    async def _fetch_update_snapshot(
        self, plugin: SitePlugin, url: str, context: PluginExecutionContext, invoker: PluginInvoker
    ) -> UpdateSnapshot:
        if not isinstance(plugin, UpdateProvider):
            raise UpdateCheckUnsupportedError()
        return await invoker.check_updates(plugin, url, context)

    async def plan_workflow(
        self,
        url: str,
        *,
        download_scope: Literal["all", "updated"] = "updated",
        plugin_overrides: PluginConfigOverrides | None = None,
        fallback_override: bool | None = None,
        plugin_id: str | None = None,
        force_plugin: bool = False,
        plugin_download_policy_overrides: PluginDownloadPolicyOverrides | None = None,
    ) -> WorkflowPlanResult:
        """Preview initial URL selection without persisting history or session changes."""
        from .workflow_plan import plan_workflow

        if download_scope not in ("all", "updated"):
            raise ValueError("download_scope must be all or updated")
        async with self._operation_lock:
            self._ensure_open()
            return await plan_workflow(
                self,
                url,
                download_scope,
                plugin_overrides,
                fallback_override,
                plugin_id,
                force_plugin,
                plugin_download_policy_overrides,
            )

    async def workflow(
        self,
        url: str,
        *,
        download_scope: Literal["all", "updated"] = "updated",
        workflow_retries: int = 1,
        workflow_retry_delay: float = 600.0,
        workflow_retry_timeout: float | None = None,
        workflow_progress_log: bool | None = None,
        plugin_overrides: PluginConfigOverrides | None = None,
        fallback_override: bool | None = None,
        plugin_id: str | None = None,
        force_plugin: bool = False,
        plugin_download_policy_overrides: PluginDownloadPolicyOverrides | None = None,
        force_image_format: ImageFormat | None = None,
    ) -> WorkflowResult:
        from .workflow import execute_workflow

        if download_scope not in ("all", "updated"):
            raise ValueError("download_scope must be all or updated")
        if workflow_progress_log is not None and type(workflow_progress_log) is not bool:
            raise ValueError("workflow_progress_log must be bool or None")
        if type(workflow_retries) is not int or workflow_retries < 0:
            raise ValueError("workflow_retries must be a non-negative integer")
        for name, value, positive in (
            ("workflow_retry_delay", workflow_retry_delay, False),
            ("workflow_retry_timeout", workflow_retry_timeout, True),
        ):
            if value is None and positive:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} must be a finite number")
            if value < 0 or (positive and value == 0):
                raise ValueError(f"{name} is outside its allowed range")
        if force_image_format not in (None, "ORIGINAL", "JPEG", "PNG", "WEBP"):
            raise ValueError("force_image_format must be ORIGINAL, JPEG, PNG, WEBP, or None")
        async with self._operation_lock:
            self._ensure_open()
            return await execute_workflow(
                self,
                url,
                download_scope,
                plugin_overrides,
                fallback_override,
                plugin_id,
                force_plugin,
                plugin_download_policy_overrides,
                force_image_format,
                workflow_retries,
                workflow_retry_delay,
                workflow_retry_timeout,
                workflow_progress_log,
            )

    def _operation(
        self,
        url: str,
        overrides: PluginConfigOverrides | None = None,
        fallback_override: bool | None = None,
        plugin_id: str | None = None,
        force_plugin: bool = False,
        download_policy_overrides: PluginDownloadPolicyOverrides | None = None,
    ) -> tuple[PluginRecord, SitePlugin, PluginExecutionContext, OperationRequestGateway, _OperationDownloadPolicy]:
        record, plugin = self._select_site_plugin(url, overrides, fallback_override, plugin_id, force_plugin)
        invoker = PluginInvoker(record.id, self.logger)
        context, gateway, policy = self._build_selected_operation(
            url,
            record,
            plugin,
            invoker,
            overrides,
            download_policy_overrides,
        )
        return record, plugin, context, gateway, policy

    def _select_site_plugin(
        self,
        url: str,
        overrides: PluginConfigOverrides | None,
        fallback_override: bool | None,
        plugin_id: str | None,
        force_plugin: bool,
    ) -> tuple[PluginRecord, SitePlugin]:
        if plugin_id is not None and fallback_override is not None:
            raise ConfigurationError("explicit plugin selection cannot be combined with fallback_override")
        fallback_enabled = self.config.fallback.generic_html.enabled if fallback_override is None else fallback_override
        return self.registry.resolve(
            url,
            fallback_enabled=fallback_enabled,
            overrides=overrides,
            plugin_id=plugin_id,
            force_plugin=force_plugin,
        )

    def _build_selected_operation(
        self,
        url: str,
        record: PluginRecord,
        plugin: SitePlugin,
        invoker: PluginInvoker,
        overrides: PluginConfigOverrides | None,
        download_policy_overrides: PluginDownloadPolicyOverrides | None,
        request_gateway: RequestGateway | None = None,
    ) -> tuple[PluginExecutionContext, OperationRequestGateway, _OperationDownloadPolicy]:
        self.registry.validate_operation_overrides(record, overrides)
        self.registry.validate_download_policy_overrides(record, download_policy_overrides)
        policy = _effective_download_policy(
            self.config,
            self.registry.effective_download_policy(record, download_policy_overrides),
        )
        settings = self.config.plugin_settings.get(record.id)
        secrets = RuntimeSecrets(record.id, settings.secrets if settings else {})
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

        operation_gateway = (request_gateway or self.gateway).operation(
            plugin_id=record.id,
            operation_url=url,
            auth_flow_factory=auth_flow_factory,
            invoker=invoker,
            request_concurrency=policy.request_concurrency,
        )
        if context is None:
            raise RuntimeError("operation context was not initialized")
        return context, operation_gateway, policy

    @asynccontextmanager
    async def _site_operation(
        self,
        url: str,
        overrides: PluginConfigOverrides | None = None,
        fallback_override: bool | None = None,
        plugin_id: str | None = None,
        force_plugin: bool = False,
        download_policy_overrides: PluginDownloadPolicyOverrides | None = None,
        request_gateway: RequestGateway | None = None,
        diagnostics_enabled: bool = True,
        selected_plugin: tuple[PluginRecord, SitePlugin] | None = None,
    ) -> AsyncIterator[
        tuple[
            PluginRecord,
            SitePlugin,
            PluginExecutionContext,
            OperationRequestGateway,
            _OperationDownloadPolicy,
            PluginInvoker,
        ]
    ]:
        record, plugin = selected_plugin or self._select_site_plugin(
            url, overrides, fallback_override, plugin_id, force_plugin
        )
        logger: DownloadLogger | None = self.logger if diagnostics_enabled else None
        invoker = PluginInvoker(record.id, logger)
        try:
            context, operation_gateway, policy = self._build_selected_operation(
                url,
                record,
                plugin,
                invoker,
                overrides,
                download_policy_overrides,
                request_gateway,
            )
        except BaseException as primary:
            await self._cleanup_site_after_failure(invoker, plugin, primary, logger)
            raise
        try:
            yield record, plugin, context, operation_gateway, policy, invoker
        except BaseException as primary:
            await self._cleanup_site_after_failure(invoker, plugin, primary, logger)
            raise
        else:
            await invoker.cleanup_after_use(plugin)

    async def _cleanup_site_after_failure(
        self,
        invoker: PluginInvoker,
        plugin: SitePlugin,
        _primary: BaseException,
        logger: DownloadLogger | None,
    ) -> None:
        try:
            await invoker.cleanup_after_use(plugin)
        except BaseException as cleanup_error:
            if logger is not None and isinstance(cleanup_error, Exception):
                await best_effort_diagnostic(
                    logger.core,
                    "site_operation_close_failed",
                    module="plugin",
                    plugin_id=invoker.plugin_id,
                    error=cleanup_error,
                    debug=True,
                )

    @staticmethod
    def _normalized_manifest(manifest: DownloadManifest, url: str) -> DownloadManifest:
        return replace(manifest, metadata={**manifest.metadata, "source_url": url})

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
        host_component = _component_limit(self.outputs.root).shorten(host_component)
        plugin_component = safe_component(record.id, max_length=self.config.output.max_component_length)
        plugin_component = _component_limit(self.outputs.root / host_component).shorten(plugin_component)
        relative = Path(host_component) / plugin_component
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
        failures: list[tuple[int, BaseException]] = []

        def start_next() -> bool:
            try:
                index, factory = next(pending)
            except StopIteration:
                return False
            active[asyncio.ensure_future(factory())] = index
            return True

        def collect(task: asyncio.Future[_ResultT]) -> None:
            index = active.pop(task)
            try:
                results[index] = task.result()
            except BaseException as exc:
                failures.append((index, exc))

        for _ in range(min(limit, len(factories))):
            start_next()
        try:
            while active:
                done, _ = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    collect(task)
                if failures:
                    # Started work is collected; queued work is intentionally never created.
                    await asyncio.gather(*active, return_exceptions=True)
                    for task in tuple(active):
                        collect(task)
                    # Task completion order must not hide an operation-stopping error.
                    raise min(failures, key=lambda failure: (_parallel_failure_priority(failure[1]), failure[0]))[1]
                for _ in range(len(done)):
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
        policy: _OperationDownloadPolicy,
        operation_url: str,
        plugin_values: Mapping[str, Mapping[str, str]],
        ledger: ImageLedger | None = None,
        additional: AdditionalFiles | None = None,
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
                policy,
                operation_url,
                plugin_values,
                ledger,
                position,
                additional,
            )

        factories = [
            lambda position=position, chapter=chapter: run_one(position, chapter)
            for position, chapter in enumerate(manifest.chapters)
        ]
        try:
            return cast(
                list[ChapterResult],
                await self._bounded(factories, policy.chapter_concurrency),
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
        policy: _OperationDownloadPolicy,
        operation_url: str,
        plugin_values: Mapping[str, Mapping[str, str]],
        ledger: ImageLedger | None = None,
        chapter_position: int = 0,
        additional: AdditionalFiles | None = None,
    ) -> ChapterResult:
        directory_context = OutputFormatContext(
            manifest,
            chapter,
            operation_url,
            plugin_id,
            plugin_values=plugin_values,
        )
        directory = allocator.chapter_directory(directory_context)
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
            if ledger is not None and (chapter_position, position) in ledger.retained:
                outcome = ledger.outcomes[chapter_position, position]
                await reporter.record(position, outcome)
                return outcome
            _start_workflow_image(ledger, chapter_position, position)
            response: RequestResponse | None = None
            transport_metadata = None

            extra = partial(_additional_image, additional, manifest=manifest, chapter=chapter, image=image)

            try:
                try:
                    await extra(AdditionalFileHookPoint.BEFORE_IMAGE_REQUEST)
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
                        url_is_locator=True,
                        debug=True,
                    )
                    fetched = await operation_gateway.execute_image_with_metadata(plugin, image, context)
                    response = fetched.response
                    transport_metadata = fetched.transport_metadata
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
                    await extra(AdditionalFileHookPoint.AFTER_IMAGE_REQUEST, response=response)
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
                    processed = await pipeline.process(
                        plugin,
                        artifact,
                        image,
                        manifest,
                        chapter,
                        transport_metadata=transport_metadata,
                    )
                    await self.events.emit(EventName.IMAGE_PROCESS_SUCCESS, EventPayload(url=image.url))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    raise _ImageJobError(FailureKind.PROCESS, exc, response) from exc
                try:
                    await extra(AdditionalFileHookPoint.BEFORE_IMAGE_SAVE, response=response, artifact=processed)
                    await self.events.emit(EventName.BEFORE_SAVE, EventPayload(url=image.url))
                    async with self.output_locks.hold(allocator.filesystem.path(directory)):
                        await allocator.refresh_directory(directory)
                        allocation = await allocator.allocate(
                            directory,
                            OutputFormatContext(
                                manifest,
                                chapter,
                                operation_url,
                                plugin_id,
                                image,
                                processed.extension or ".jpeg",
                                resolve_original_filename(image, response),
                                plugin_values,
                            ),
                        )
                        path = allocator.filesystem.path(allocation.relative_path)
                        if allocation.should_write:
                            try:
                                path = allocator.filesystem.write_bytes_atomic(allocation.relative_path, processed.data)
                            except BaseException:
                                await self._settle_allocation(allocation, success=False)
                                raise
                            await self._commit_image_allocation(
                                allocation, ledger, chapter_position, position, image, path
                            )
                        _remember_image(
                            ledger,
                            chapter_position,
                            position,
                            ImageOutcome(
                                image,
                                ImageOutcomeKind.SAVED if allocation.should_write else ImageOutcomeKind.SKIPPED,
                                str(path),
                            ),
                        )
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
                        url_is_locator=True,
                        debug=True,
                    )
                outcome = ImageOutcome(
                    image, ImageOutcomeKind.SAVED if allocation.should_write else ImageOutcomeKind.SKIPPED, str(path)
                )
                await extra(
                    AdditionalFileHookPoint.AFTER_IMAGE_SAVE,
                    response=response,
                    artifact=processed,
                    image_outcome=outcome,
                )
            except asyncio.CancelledError:
                raise
            except _ImageJobError as exc:
                info, response_url, http_status, failure_path = _failure_info(
                    _workflow_image_error(exc.cause, ledger),
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
                    url_is_locator=True,
                    debug=True,
                )
                await best_effort_diagnostic(
                    self.logger.error_detail,
                    exc.cause,
                    chapter_id=chapter_id,
                    url=image.url,
                    path=failure_path,
                    module="image",
                    url_is_locator=True,
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
                _remember_image(ledger, chapter_position, position, outcome)
                await reporter.record(position, outcome)
                if _is_fatal_image_error(exc.cause, self.config.download.continue_on_image_error, ledger):
                    exc.cause._image_failure_reported = True
                    exc.cause._image_failure_context = {
                        "stage": _IMAGE_FAILURE_STAGES[exc.kind],
                        "transport": transport,
                        "image_url": image.url,
                        "response_url": response_url,
                        "http_status": http_status,
                        "output_path": info.output_path,
                    }
                    _raise_image_error(exc.cause, ledger)
                return outcome
            _remember_image(ledger, chapter_position, position, outcome)
            await reporter.record(position, outcome)
            return outcome

        factories = [
            lambda position=position, image=image: run_one(position, image)
            for position, image in enumerate(chapter.images)
        ]
        try:
            bounded = await self._bounded(factories, policy.image_concurrency_per_chapter)
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
    async def _commit_image_allocation(
        allocation: OutputAllocation,
        ledger: ImageLedger | None,
        cp: int,
        ip: int,
        image: ImageResource,
        path: Path,
    ) -> None:
        try:
            await DownloadService._settle_allocation(allocation, success=True)
        except asyncio.CancelledError:
            _remember_image(ledger, cp, ip, ImageOutcome(image, ImageOutcomeKind.SAVED, str(path)))
            raise

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

    async def _empty_reporter(
        self,
        manifest: DownloadManifest,
        record: PluginRecord,
        url: str,
        plugin_values: Mapping[str, Mapping[str, str]],
    ) -> None:
        chapter = Chapter(0, manifest.title)
        outputs = self._output_filesystem(record, url)
        directory = OutputAllocator(outputs, self.config).chapter_directory(
            OutputFormatContext(manifest, chapter, url, record.id, plugin_values=plugin_values)
        )
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
                    if self._persist_cookies_on_close:
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
