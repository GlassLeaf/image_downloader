"""artifact pipeline runtime responsibilities."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from dataclasses import replace
from pathlib import PurePosixPath
from urllib.parse import urlparse

from ..configuration.models import AppConfig, ImageFormat
from ..exceptions import ConfigurationError, ImageContentTypeError, ImageMimeMismatchError
from ..media.image_processor import ImageProcessor
from ..media.processor_chain import PreparedProcessor
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
from ..plugins.runtime import PluginRuntime, safe_app_settings
from ..ports import SitePlugin, TransformContext
from ..privacy.log_safety import safe_url

_IMAGE_FORMATS = frozenset(("ORIGINAL", "JPEG", "PNG", "WEBP"))
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
        force_image_format: ImageFormat | None = None,
    ) -> None:
        if force_image_format not in (None, *_IMAGE_FORMATS):
            raise ValueError("force_image_format must be ORIGINAL, JPEG, PNG, WEBP, or None")
        self.config = config
        self.registry = registry
        self.site_record = site_record
        self.overrides = overrides
        self.core = image_processor
        self.logger = logger
        self.invoker = invoker
        self.processors = processors
        self.force_image_format = force_image_format

    @staticmethod
    def _catalog(record: PluginRecord) -> Mapping[str, object] | None:
        return record.catalog.as_json() if record.catalog is not None else None

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
        permitted_sites = self.config.image_processors.transport_metadata_access.get(processor_id, ())
        return value if self.site_record.id in permitted_sites else self._redacted_transport_metadata(value)

    async def process(
        self,
        plugin: SitePlugin,
        artifact: ImageArtifact,
        image: ImageResource,
        manifest: DownloadManifest,
        chapter: Chapter,
        *,
        transport_metadata: ImageTransportMetadata | None = None,
    ) -> ImageArtifact:
        context = TransformContext(
            image,
            self.registry.effective_config(self.site_record, self.overrides),
            safe_app_settings(self.config),
            self.site_record.manifest.value,
            self._catalog(self.site_record),
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
        )
        await asyncio.to_thread(self._validate_input, transformed)
        options = self._effective_save_options(image)
        original = self._is_original(options)
        if original:
            self._validate_original_options(options)
            current = transformed
        else:
            current = await self._normalize(transformed, options, "core.decode-normalize")
        processor_changed = False
        for binding in self.processors:
            if isinstance(binding, str):
                await best_effort_diagnostic(
                    self.logger.core,
                    "processor_disabled", module="processor", chapter_id=chapter_id, plugin_id=binding, debug=True
                )
                continue
            processor_context = TransformContext(
                image,
                binding.config,
                binding.app_settings,
                binding.record.manifest.value,
                self._catalog(binding.record),
                manifest,
                chapter,
                site_manifest=self.site_record.manifest.value,
                site_catalog=self._catalog(self.site_record),
                transport_metadata=self._processor_transport_metadata(binding.plugin_id, transport_metadata),
            )
            previous = current
            async with binding.lock:
                current = await PluginInvoker(binding.plugin_id, self.logger).transform_processor(
                    binding.instance,
                    current,
                    processor_context,
                    chapter_id=chapter_id,
                )
            processor_changed = processor_changed or current != previous
        if original:
            await asyncio.to_thread(self._validate_input, current)
            final = replace(
                current,
                extension=self._original_extension(current),
                history=(*current.history, "core.original-preserve"),
            )
        else:
            final = (
                await self._normalize(current, options, "core.final-validate")
                if processor_changed
                else replace(current, history=(*current.history, "core.final-validate"))
            )
        await best_effort_diagnostic(
            self.logger.core,
            "image_processed",
            module="image",
            chapter_id=chapter_id,
            url=final.source_url,
            bytes_count=len(final.data),
            plugin_id=self.site_record.id,
            url_is_locator=True,
            debug=True,
        )
        return final

    def _effective_save_options(self, image: ImageResource) -> ImageSaveOptions:
        """Resolve the force CLI, plugin, and configuration format precedence."""
        if self.force_image_format is not None:
            if self.force_image_format == "ORIGINAL":
                # A forced pass-through must not retain a plugin's prospective
                # extension or encoder settings for a different target format.
                return ImageSaveOptions(format="ORIGINAL")
            return replace(image.save_options, format=self.force_image_format, extension=None)
        if image.save_options.format:
            return image.save_options
        return replace(image.save_options, format=self.config.output.image_format)

    @staticmethod
    def _is_original(options: ImageSaveOptions) -> bool:
        return options.format is not None and options.format.upper() == "ORIGINAL"

    @staticmethod
    def _validate_original_options(options: ImageSaveOptions) -> None:
        if options.extension is not None or any(
            value is not None
            for value in (
                options.quality,
                options.optimize,
                options.progressive,
                options.lossless,
                options.compress_level,
            )
        ) or options.exif:
            raise ConfigurationError("ORIGINAL image format cannot use an extension or encoder options")

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
        declared = artifact.content_type.lower().split(";", 1)[0].strip()
        if self.config.media.input_validation in {"content_type", "both"} and (
            declared.startswith("text/") or declared in {"application/json", "application/xml"}
        ):
            raise ImageContentTypeError("image request returned a non-image content type")
        if self.config.media.input_validation in {"decode", "both"} or (
            declared.startswith("image/") and self.config.media.content_type_mismatch == "error"
        ):
            detected = self.core.inspect(artifact.data, max_pixels=self.config.media.max_image_pixels)
            if (
                declared.startswith("image/")
                and declared != detected
                and self.config.media.content_type_mismatch == "error"
            ):
                raise ImageMimeMismatchError("declared image MIME does not match image data")

    async def _normalize(
        self,
        artifact: ImageArtifact,
        options: ImageSaveOptions,
        history: str,
    ) -> ImageArtifact:
        data, extension = await asyncio.to_thread(
            self.core.process,
            artifact.data,
            source_url=artifact.source_url,
            content_type=artifact.content_type,
            options=options,
            max_pixels=self.config.media.max_image_pixels,
        )
        return replace(artifact, data=data, extension=extension, history=(*artifact.history, history))
