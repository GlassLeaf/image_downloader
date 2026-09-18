"""artifact pipeline runtime responsibilities."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import replace

from ..configuration.models import AppConfig
from ..exceptions import (
    DownloaderError,
)
from ..media.image_processor import ImageProcessor
from ..media.processor_chain import PreparedProcessor
from ..models import (
    Chapter,
    DownloadManifest,
    ImageArtifact,
    ImageResource,
    ImageSaveOptions,
)
from ..observability.diagnostic_safety import best_effort_diagnostic
from ..observability.logging import (
    DownloadLogger,
)
from ..plugins.lifecycle import PluginRecord
from ..plugins.plugin_invoker import PluginInvoker
from ..plugins.plugin_manifest import PluginConfigOverrides
from ..plugins.runtime import PluginRuntime, safe_app_settings
from ..ports import SitePlugin, TransformContext


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
    ) -> None:
        self.config = config
        self.registry = registry
        self.site_record = site_record
        self.overrides = overrides
        self.core = image_processor
        self.logger = logger
        self.invoker = invoker
        self.processors = processors

    @staticmethod
    def _catalog(record: PluginRecord) -> Mapping[str, object] | None:
        return record.catalog.as_json() if record.catalog is not None else None

    async def process(
        self,
        plugin: SitePlugin,
        artifact: ImageArtifact,
        image: ImageResource,
        manifest: DownloadManifest,
        chapter: Chapter,
    ) -> ImageArtifact:
        context = TransformContext(
            image,
            self.registry.effective_config(self.site_record, self.overrides),
            safe_app_settings(self.config),
            self.site_record.manifest.value,
            self._catalog(self.site_record),
            manifest,
            chapter,
        )
        chapter_id = str(chapter.number)
        transformed = await self.invoker.transform_image(
            plugin,
            artifact,
            context,
            chapter_id=chapter_id,
        )
        await asyncio.to_thread(self._validate_input, transformed)
        options = (
            image.save_options
            if image.save_options.format
            else replace(image.save_options, format=self.config.output.image_format)
        )
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
            debug=True,
        )
        return final

    def _validate_input(self, artifact: ImageArtifact) -> None:
        declared = artifact.content_type.lower().split(";", 1)[0].strip()
        if self.config.media.input_validation in {"content_type", "both"} and (
            declared.startswith("text/") or declared in {"application/json", "application/xml"}
        ):
            raise DownloaderError("image request returned a non-image content type")
        if self.config.media.input_validation in {"decode", "both"} or (
            declared.startswith("image/") and self.config.media.content_type_mismatch == "error"
        ):
            detected = self.core.inspect(artifact.data, max_pixels=self.config.media.max_image_pixels)
            if (
                declared.startswith("image/")
                and declared != detected
                and self.config.media.content_type_mismatch == "error"
            ):
                raise DownloaderError("declared image MIME does not match image data")

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
