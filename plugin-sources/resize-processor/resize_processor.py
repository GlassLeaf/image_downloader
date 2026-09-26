"""Pillow-backed PNG image-processor v3 reference implementation."""

from __future__ import annotations

from collections.abc import Mapping
from io import BytesIO

from PIL import Image

from image_downloader import ImageArtifact, TransformContext


class ResizeProcessor:
    """Constrain an image to configured bounds and encode a PNG artifact."""

    def validate_config(self, config: Mapping[str, object], app_settings: Mapping[str, object]) -> None:
        del app_settings
        for key in ("max_width", "max_height"):
            value = config.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"resize processor requires positive integer config.{key}")

    async def transform(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact:
        width = context.config["max_width"]
        height = context.config["max_height"]
        assert isinstance(width, int) and isinstance(height, int)  # validated before invocation
        try:
            with Image.open(BytesIO(artifact.data)) as source:
                image = source.copy()
            image.thumbnail((width, height), Image.Resampling.LANCZOS)
            destination = BytesIO()
            image.save(destination, format="PNG")
        except (OSError, ValueError) as exc:
            raise ValueError("resize processor could not decode or encode image data") from exc
        return ImageArtifact(
            data=destination.getvalue(),
            content_type="image/png",
            source_url=artifact.source_url,
            image_id=artifact.image_id,
            extension="png",
            history=(*artifact.history, f"resize:{image.width}x{image.height}"),
        )
