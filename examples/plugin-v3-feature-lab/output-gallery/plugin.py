from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from image_downloader import DownloadManifest, ImageSaveOptions, PluginExecutionContext, PluginFormatContext

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
                        save_options=(
                            ImageSaveOptions(
                                format="JPEG", extension=".jpg", quality=70, optimize=True, progressive=True, exif=True
                            )
                            if image.index == 1
                            else ImageSaveOptions(format="PNG", compress_level=4, optimize=True, exif=False)
                        ),
                    )
                    for image in chapter.images
                ),
            )
            for chapter in manifest.chapters
        )
        return replace(manifest, chapters=chapters)

    def output_format_values(self, context: PluginFormatContext) -> Mapping[str, str]:
        return {"VARIANT": "_lab"}
