"""Download lifecycle, manifest acquisition and processor preparation."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse

from ..configuration.hosts import normalize_host, site_file_name
from ..configuration.models import AppConfig
from ..exceptions import (
    AuthenticationError,
    ConfigurationError,
    ImageDownloaderError,
    PluginError,
    StorageError,
    error_info_for,
)
from ..media.artifact_pipeline import ArtifactPipeline
from ..media.processor_chain import OperationProcessorChain
from ..models import (
    AdditionalFileHookPoint,
    Chapter,
    DownloadManifest,
    DownloadResult,
)
from ..observability.chapter_reporter import ChapterReporter
from ..observability.diagnostic_safety import best_effort_diagnostic
from ..observability.events import EventName, EventPayload
from ..observability.scope import OperationDiagnosticsScope
from ..output.output_allocator import OutputAllocator, OutputFormatContext
from ..plugins.format_values import PluginFormatValueParticipant, collect_plugin_format_values
from ..plugins.lifecycle import PluginRecord
from ..plugins.plugin_invoker import PluginInvoker
from ..plugins.plugin_manifest import PluginConfigOverrides
from ..plugins.runtime import PluginRuntime, safe_app_settings
from ..ports import SitePlugin
from ..storage import FileSystem, safe_component
from .additional_files import AdditionalFiles
from .chapter_executor import ChapterExecutor
from .dependencies import _RuntimeDependencies
from .download_context import DownloadContext
from .image_executor import ImageOperationError
from .requests import DownloadRequest
from .site_operation import SiteOperation, SiteOperationFactory
from .workflow_retry import ImageLedger


class DownloadExecutor:
    """Coordinate a download without owning the public service lifecycle."""

    def __init__(
        self,
        config: AppConfig,
        registry: PluginRuntime,
        dependencies: _RuntimeDependencies,
        operations: SiteOperationFactory,
    ) -> None:
        self.config, self.registry, self.operations = config, registry, operations
        self.outputs = dependencies.outputs
        self.events, self.logger = dependencies.events, dependencies.logger
        self.image_processor, self.output_locks = dependencies.image_processor, dependencies.output_locks
        self.chapters = ChapterExecutor(config, self.events, self.logger, self.output_locks)

    async def execute(
        self,
        request: DownloadRequest,
        diagnostics: OperationDiagnosticsScope,
        ledger: ImageLedger | None = None,
    ) -> DownloadResult:
        try:
            async with self.operations.open(
                request.url,
                request.plugin_overrides,
                request.fallback_override,
                request.plugin_id,
                request.force_plugin,
                request.plugin_download_policy_overrides,
            ) as site:
                record, plugin, context = site.record, site.plugin, site.context
                invoker = site.invoker
                diagnostics.capture(self.operations.python_log_namespaces(record, include_processors=True))
                async with OperationProcessorChain(
                    self.registry,
                    self.config.image_processors.chain,
                    request.plugin_overrides,
                    self.logger,
                ) as processors:
                    await best_effort_diagnostic(
                        self.logger.core,
                        "operation_started",
                        module="runtime",
                        url=request.url,
                        plugin_id=record.id,
                        debug=True,
                    )
                    await best_effort_diagnostic(
                        self.logger.core,
                        "plugin_selected",
                        module="plugin",
                        url=request.url,
                        plugin_id=record.id,
                        debug=True,
                    )
                    plugin_values = self._collect_output_format_values(
                        request.url,
                        record,
                        plugin,
                        request.plugin_overrides,
                        invoker,
                        processors,
                    )
                    allocator = OutputAllocator(self._output_filesystem(record, request.url), self.config)
                    additional = AdditionalFiles(
                        plugin,
                        context,
                        allocator,
                        record.id,
                        request.url,
                        plugin_values,
                        self.config.network.max_response_bytes,
                        self.logger,
                        self.output_locks,
                        ledger,
                    )
                    try:
                        result = await self._execute_manifest(
                            additional,
                            site,
                            request,
                            ledger,
                            plugin_values,
                            processors,
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
            await self.events.emit(outcome, EventPayload(url=request.url))
            await self.events.emit(EventName.DOWNLOAD_COMPLETE, EventPayload(url=request.url))
            await best_effort_diagnostic(
                self.logger.core, "download_finished", module="download", url=request.url, debug=True
            )
            return result
        except asyncio.CancelledError:
            await best_effort_diagnostic(
                self.logger.core, "download_cancelled", module="runtime", url=request.url, debug=True
            )
            raise
        except ImageOperationError as exc:
            await self._record_operation_failure(request.url, exc.cause, image_reported=True)
            raise exc.cause from None
        except Exception as exc:
            await self._record_operation_failure(request.url, exc)
            raise

    async def _execute_manifest(
        self,
        additional: AdditionalFiles,
        site: SiteOperation,
        request: DownloadRequest,
        ledger: ImageLedger | None,
        plugin_values: Mapping[str, Mapping[str, str]],
        processors: OperationProcessorChain,
    ) -> DownloadResult:
        await additional.initialize()
        await additional.run(AdditionalFileHookPoint.BEFORE_MANIFEST)
        manifest = self.operations.normalize_manifest(
            await site.invoker.inspect(site.plugin, request.url, site.context), request.url
        )
        if ledger is not None:
            ledger.begin(manifest, site.record.id)
        await additional.flush_pending(manifest)
        for chapter in manifest.chapters:
            await additional.run(AdditionalFileHookPoint.AFTER_MANIFEST, manifest=manifest, chapter=chapter)
        self._validate_empty_manifest_policy(manifest)
        if not manifest.chapters:
            await self._empty_reporter(manifest, site.record, request.url, plugin_values)
            result = DownloadResult(request.url, manifest, ())
        else:
            allocator = additional.allocator
            pipeline = ArtifactPipeline(
                self.config,
                self.registry,
                site.record,
                request.plugin_overrides,
                self.image_processor,
                self.logger,
                site.invoker,
                processors.bindings,
                force_image_format=request.force_image_format,
            )
            results = await self.chapters.execute(
                DownloadContext(
                    site,
                    manifest,
                    allocator,
                    pipeline,
                    request.url,
                    plugin_values,
                    ledger,
                    additional,
                )
            )
            result = DownloadResult(request.url, manifest, tuple(results))
        for chapter_result in result.chapters:
            await additional.run(
                AdditionalFileHookPoint.AFTER_DOWNLOAD,
                manifest=manifest,
                chapter=chapter_result.chapter,
                chapter_result=chapter_result,
            )
        return replace(result, additional_files=tuple(ledger.additional_files if ledger else additional.outcomes))

    def _validate_empty_manifest_policy(self, manifest: DownloadManifest) -> None:
        """Resolve empty-result permissions once, before starting chapter downloads."""
        if not manifest.chapters:
            if not self.config.download.allow_empty_chapter_manifest or manifest.allow_empty_chapters is False:
                raise PluginError("plugin returned an empty manifest")
            return
        if any(not chapter.images and chapter.allow_empty_images is False for chapter in manifest.chapters):
            raise PluginError("plugin returned an empty image list")

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
        host_component = self.outputs.shorten_component(host_component)
        plugin_component = safe_component(record.id, max_length=self.config.output.max_component_length)
        plugin_component = self.outputs.shorten_component(plugin_component, parent=host_component)
        relative = Path(host_component) / plugin_component
        self.outputs.ensure_directory(relative)
        return FileSystem(self.outputs.path(relative))

    async def _record_operation_failure(self, url: str, error: Exception, *, image_reported: bool = False) -> None:
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
        if not image_reported and not (isinstance(error, ImageDownloaderError) and error.image_failure is not None):
            await self.events.emit(event, payload)
        await self.events.emit(EventName.DOWNLOAD_FAILED, payload)
        await best_effort_diagnostic(
            self.logger.core, "operation_failed", module="runtime", url=url, error=error, debug=True
        )
        await best_effort_diagnostic(self.logger.error_detail, error, url=url, module="runtime")

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
