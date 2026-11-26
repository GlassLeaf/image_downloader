from __future__ import annotations

import io
from collections.abc import Mapping
from dataclasses import replace

from PIL import Image

from image_downloader import (
    DownloadManifest,
    ImageArtifact,
    ImageSaveOptions,
    PluginExecutionContext,
    PluginFormatContext,
    TransformContext,
)

from .base import BaseGallery


class LabPlugin(BaseGallery):
    slug = "output-gallery"

    async def inspect(self, url: str, context: PluginExecutionContext) -> DownloadManifest:
        manifest = await super().inspect(url, context)
        chapters = tuple(
            replace(
                chapter,
                images=tuple(
                    replace(
                        image,
                        original_filename=f"cover-{image.image_id}.png",
                    )
                    for image in chapter.images
                ),
            )
            for chapter in manifest.chapters
        )
        return replace(manifest, chapters=chapters)

    def transform_image(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact:
        target = "JPEG" if context.index == 1 else "PNG"
        with Image.open(io.BytesIO(artifact.data)) as source:
            image = source.convert("RGB") if target == "JPEG" else source.copy()
            output = io.BytesIO()
            image.save(output, format=target, exif=source.getexif().tobytes())
        return replace(
            artifact,
            data=output.getvalue(),
            content_type=Image.MIME[target],
            extension=".jpg" if target == "JPEG" else ".png",
        )

    def image_save_options(self, artifact: ImageArtifact, context: TransformContext) -> ImageSaveOptions:
        if context.index == 1:
            return ImageSaveOptions(quality=70, optimize=True, progressive=True, exif=True)
        return ImageSaveOptions(compress_level=4, optimize=True, exif=False)

    def output_format_values(self, context: PluginFormatContext) -> Mapping[str, str]:
        return {"VARIANT": "_lab"}
