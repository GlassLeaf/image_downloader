"""Minimal image-processor v3 contract example."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from image_downloader import ImageArtifact, TransformContext


class ArtifactHistoryProcessor:
    """Append a validated label without mutating the input artifact."""

    def validate_config(self, config: Mapping[str, object], app_settings: Mapping[str, object]) -> None:
        del app_settings
        label = config.get("label")
        if not isinstance(label, str) or not label.strip():
            raise ValueError("artifact history processor requires non-empty config.label")

    async def transform(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact:
        label = context.config["label"]
        assert isinstance(label, str)  # validated before this hook is invoked
        return replace(artifact, history=(*artifact.history, label))
