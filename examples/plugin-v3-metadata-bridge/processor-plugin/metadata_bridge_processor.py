"""v3 sample: consume allow-listed site ``plugin_data`` without leaking it."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

import image_downloader as plugin_api
from image_downloader import ImageArtifact, TransformContext


class MetadataBridgeProcessor:
    """Select a local processing profile from non-secret, allow-listed data."""

    def validate_config(self, config: Mapping[str, object], app_settings: Mapping[str, object]) -> None:
        del app_settings
        if "structured_plugin_data" not in getattr(plugin_api, "PLUGIN_API_FEATURES", ()):
            raise ValueError("metadata bridge requires a runtime with structured_plugin_data")
        if config:
            raise ValueError("metadata bridge processor does not accept configuration")

    def transform(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact:
        # Inspection-time structured data is shared independently of transport permissions.
        if context.plugin_data.image.get("variant") not in {"full", "preview"}:
            return replace(artifact, history=(*artifact.history, "metadata-bridge:no-structured-data"))
        metadata = context.transport_metadata
        if metadata is None:
            return replace(artifact, history=(*artifact.history, "metadata-bridge:no-transport-metadata"))
        if metadata.is_redacted:
            return replace(artifact, history=(*artifact.history, "metadata-bridge:transport-metadata-redacted"))

        # The allow-list has granted this processor the selected site's raw
        # request-time data.  Use it only to choose behavior; do not copy raw
        # plugin_data into the artifact, output name, result, event, or log.
        variant = metadata.plugin_data.get("variant")
        orientation = metadata.plugin_data.get("orientation")
        if variant not in {"full", "preview"} or orientation not in {"1", "3", "6", "8"}:
            return replace(artifact, history=(*artifact.history, "metadata-bridge:profile-not-applied"))
        return replace(artifact, history=(*artifact.history, "metadata-bridge:profile-applied"))
