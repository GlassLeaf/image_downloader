"""artifact pipeline runtime responsibilities."""

from __future__ import annotations

import asyncio
import re
from dataclasses import replace
from pathlib import PurePosixPath
from urllib.parse import urlparse

from ..configuration.models import AppConfig, ImageFormat
from ..exceptions import ImageContentTypeError, ImageDecodeError, ImageMimeMismatchError
from ..media.image_processor import ImageEncodingOptions, ImageProcessor
from ..media.pipeline_binding import PreparedPipelineBinding
from ..media.processor_chain import PreparedProcessor
from ..media.transform_executor import TransformExecutor
from ..models import (
    Chapter,
    DownloadManifest,
    ImageArtifact,
    ImageResource,
    ImageSaveOptions,
    ImageTransportMetadata,
    TransportCookie,
    TransportRequestMetadata,
)
from ..observability.diagnostic_safety import best_effort_diagnostic
from ..observability.logging import DownloadLogger
from ..plugins.lifecycle import PluginRecord
from ..plugins.plugin_invoker import PluginInvoker
from ..plugins.plugin_manifest import PluginConfigOverrides
from ..plugins.runtime import PluginRuntime
from ..ports import SitePlugin, TransformContext
from ..privacy.log_safety import safe_url
from .save_policy import FORMAT_MIMES, aggregate_save_options, resolve_save_plan

_SAFE_EXTENSION = re.compile(r"\.?([A-Za-z0-9]{1,16})\Z")
_CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": ".jpeg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
    "image/x-icon": ".ico",
    "image/vnd.microsoft.icon": ".ico",
    "image/avif": ".avif",
    "image/heic": ".heic",
    "image/heif": ".heif",
    "image/svg+xml": ".svg",
}


class ArtifactPipeline:
    def __init__(
        self,
        config: AppConfig,
        registry: PluginRuntime,
        site_record: PluginRecord,
        overrides: PluginConfigOverrides | None,
        image_processor: ImageProcessor,
        logger: DownloadLogger,
        invoker: PluginInvoker,
        processors: tuple[PreparedProcessor | str, ...],
        *,
        image_format: ImageFormat | None = None,
        image_quality: int | None = None,
        transform_executor: TransformExecutor,
    ) -> None:
        # Preserve the advanced public constructor; core execution supplies its
        # already-prepared binding through the internal entry point below.
        from .pipeline_binding import prepare_runtime_binding

        binding = prepare_runtime_binding(config, registry, site_record, overrides, image_format, image_quality)
        self._initialize(binding, image_processor, logger, invoker, processors, transform_executor)

    @classmethod
    def _from_binding(
        cls,
        binding: PreparedPipelineBinding,
        image_processor: ImageProcessor,
        logger: DownloadLogger,
        invoker: PluginInvoker,
        processors: tuple[PreparedProcessor | str, ...],
        *,
        transform_executor: TransformExecutor,
    ) -> ArtifactPipeline:
        pipeline = cls.__new__(cls)
        pipeline._initialize(binding, image_processor, logger, invoker, processors, transform_executor)
        return pipeline

    def _initialize(
        self,
        binding: PreparedPipelineBinding,
        image_processor: ImageProcessor,
        logger: DownloadLogger,
        invoker: PluginInvoker,
        processors: tuple[PreparedProcessor | str, ...],
        transform_executor: TransformExecutor,
    ) -> None:
        self.binding = binding
        self.core = image_processor
        self.logger = logger
        self.invoker = invoker
        self.processors = processors
        self.image_format = binding.image_format
        self.image_quality = binding.image_quality
        self.transform_executor = transform_executor

    @staticmethod
    def _redacted_transport_metadata(value: ImageTransportMetadata) -> ImageTransportMetadata:
        def redact_request(request: TransportRequestMetadata) -> TransportRequestMetadata:
            return TransportRequestMetadata(
                safe_url(request.url),
                {name: tuple("[REDACTED]" for _ in values) for name, values in request.headers.items()},
                tuple(TransportCookie(cookie.name, "[REDACTED]") for cookie in request.cookies),
            )

        return ImageTransportMetadata(
            redact_request(value.initial_request),
            redact_request(value.final_request),
            safe_url(value.response_url),
            {name: tuple("[REDACTED]" for _ in values) for name, values in value.response_headers.items()},
            {key: "[REDACTED]" for key in value.plugin_data},
            is_redacted=True,
        )

    def _processor_transport_metadata(
        self,
        processor_id: str,
        value: ImageTransportMetadata | None,
    ) -> ImageTransportMetadata | None:
        if value is None:
            return None
        return value if processor_id in self.binding.metadata_processors else self._redacted_transport_metadata(value)

    async def process(
        self,
        plugin: SitePlugin,
        artifact: ImageArtifact,
        image: ImageResource,
        manifest: DownloadManifest,
        chapter: Chapter,
        *,
        transport_metadata: ImageTransportMetadata | None = None,
        reporter_id: str | None = None,
    ) -> ImageArtifact:
        context = TransformContext(
            image,
            self.binding.config,
            self.binding.app_settings,
            self.binding.manifest,
            self.binding.catalog,
            manifest,
            chapter,
            transport_metadata=transport_metadata,
        )
        chapter_id = str(chapter.number)
        transformed = await self.invoker.transform_image(
            plugin,
            artifact,
            context,
            chapter_id=chapter_id,
            executor=self.transform_executor,
        )
        await asyncio.to_thread(self._validate_input, transformed)
        current = transformed
        providers: list[tuple[object, PluginInvoker, TransformContext]] = [(plugin, self.invoker, context)]
        for binding in self.processors:
            if isinstance(binding, str):
                await best_effort_diagnostic(
                    self.logger.core,
                    "processor_disabled",
                    module="processor",
                    chapter_id=chapter_id,
                    plugin_id=binding,
                    debug=True,
                )
                continue
            processor_context = TransformContext(
                image,
                binding.config,
                binding.app_settings,
                binding.plugin_manifest,
                binding.catalog,
                manifest,
                chapter,
                site_manifest=self.binding.manifest,
                site_catalog=self.binding.catalog,
                transport_metadata=self._processor_transport_metadata(binding.plugin_id, transport_metadata),
            )
            current = await PluginInvoker(binding.plugin_id, self.logger).transform_processor(
                binding.instance,
                current,
                processor_context,
                chapter_id=chapter_id,
                executor=self.transform_executor,
            )
            providers.append((binding.instance, PluginInvoker(binding.plugin_id, self.logger), processor_context))
        if current.data is not transformed.data or current.content_type != transformed.content_type:
            await asyncio.to_thread(self._validate_input, current)
        requirements = []
        for provider, invoker, save_context in providers:
            options = await invoker.image_save_options(
                provider,
                current,
                save_context,
                chapter_id=chapter_id,
                executor=self.transform_executor,
            )
            if options is not None:
                requirements.append(options)
        combined = aggregate_save_options(requirements)
        final = await self._save_artifact(current, combined, reporter_id or chapter_id)
        await best_effort_diagnostic(
            self.logger.core,
            "image_processed",
            module="image",
            chapter_id=chapter_id,
            url=final.source_url,
            bytes_count=len(final.data),
            plugin_id=self.binding.plugin_id,
            url_is_locator=True,
            debug=True,
        )
        return final

    async def _save_artifact(
        self, artifact: ImageArtifact, options: ImageSaveOptions, chapter_id: str
    ) -> ImageArtifact:
        info = None
        needs_info = (
            options.min_width is not None
            or options.min_height is not None
            or self.image_format not in (None, "ORIGINAL")
            or self.image_quality is not None
            or any(getattr(options, name) is not None for name in ("optimize", "progressive", "compress_level", "exif"))
        )
        if needs_info:
            try:
                info = await asyncio.to_thread(
                    self.core.describe, artifact.data, max_pixels=self.binding.max_image_pixels
                )
            except ImageDecodeError:
                # Weak input-validation modes may intentionally accept Pillow-unsupported data.
                # A requested encode will still perform its normal validation and fail if needed.
                pass
        plan = resolve_save_plan(options, info, artifact.content_type, self.image_format, self.image_quality)
        for warning in plan.warnings:
            await best_effort_diagnostic(
                self.logger.core,
                "image_save_policy_unmet",
                module="image",
                chapter_id=chapter_id,
                url=artifact.source_url,
                url_is_locator=True,
                include_chapter=True,
                policy_field=warning.field,
                requested=warning.requested,
                actual=warning.actual,
                reason=warning.reason,
            )
        if plan.encoding is None:
            if self.image_format not in (None, "ORIGINAL"):
                mime = FORMAT_MIMES[self.image_format]
                artifact = replace(artifact, content_type=mime, extension=_CONTENT_TYPE_EXTENSIONS[mime])
            return replace(
                artifact,
                extension=self._original_extension(artifact),
                history=(*artifact.history, "core.original-preserve"),
            )
        return await self._normalize(artifact, plan.encoding, "core.final-encode")

    @staticmethod
    def _safe_extension(value: str) -> str | None:
        match = _SAFE_EXTENSION.fullmatch(value.strip())
        return f".{match.group(1).lower()}" if match is not None else None

    @classmethod
    def _original_extension(cls, artifact: ImageArtifact) -> str:
        if artifact.extension is not None:
            extension = cls._safe_extension(artifact.extension)
            if extension is not None:
                return extension
        content_type = artifact.content_type.split(";", 1)[0].strip().lower()
        if content_type in _CONTENT_TYPE_EXTENSIONS:
            return _CONTENT_TYPE_EXTENSIONS[content_type]
        locator_suffix = PurePosixPath(urlparse(artifact.source_url).path).suffix
        if locator_suffix:
            extension = cls._safe_extension(locator_suffix)
            if extension is not None:
                return extension
        return ".bin"

    def _validate_input(self, artifact: ImageArtifact) -> None:
        if not artifact.data:
            raise ImageDecodeError("image data is empty")
        declared = artifact.content_type.lower().split(";", 1)[0].strip()
        unspecified = declared in {"", "application/octet-stream"}
        if self.binding.input_validation in {"content_type", "both"} and (
            not unspecified and declared not in _CONTENT_TYPE_EXTENSIONS
        ):
            raise ImageContentTypeError("image request returned a non-image content type")
        if self.binding.input_validation in {"decode", "both"} or (
            declared.startswith("image/") and self.binding.content_type_mismatch == "error"
        ):
            detected = self.core.inspect(artifact.data, max_pixels=self.binding.max_image_pixels)
            if declared.startswith("image/") and declared != detected and self.binding.content_type_mismatch == "error":
                raise ImageMimeMismatchError("declared image MIME does not match image data")

    async def _normalize(
        self,
        artifact: ImageArtifact,
        options: ImageEncodingOptions,
        history: str,
    ) -> ImageArtifact:
        data, extension = await asyncio.to_thread(
            self.core.process,
            artifact.data,
            source_url=artifact.source_url,
            content_type=artifact.content_type,
            options=options,
            max_pixels=self.binding.max_image_pixels,
        )
        return replace(
            artifact,
            data=data,
            content_type=FORMAT_MIMES[options.format or ""],
            extension=extension,
            history=(*artifact.history, history),
        )
