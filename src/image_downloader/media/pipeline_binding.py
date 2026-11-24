"""Immutable transform settings resolved once for one download attempt."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from ..configuration.models import AppConfig, ImageFormat
from ..immutable import freeze_json

if TYPE_CHECKING:
    from ..plugins.lifecycle import PluginRecord
    from ..plugins.plugin_manifest import PluginConfigOverrides
    from ..plugins.runtime import PluginRuntime


def snapshot(value: Mapping[str, object]) -> Mapping[str, object]:
    return cast(Mapping[str, object], freeze_json(value))


@dataclass(frozen=True, slots=True)
class PreparedPipelineBinding:
    plugin_id: str
    config: Mapping[str, object]
    app_settings: Mapping[str, object]
    manifest: Mapping[str, object]
    catalog: Mapping[str, object] | None
    image_format: ImageFormat
    input_validation: str
    content_type_mismatch: str
    max_image_pixels: int | None
    metadata_processors: frozenset[str]
    force_image_format: ImageFormat | None

    @classmethod
    def prepare(
        cls,
        config: AppConfig,
        plugin_id: str,
        plugin_config: Mapping[str, object],
        app_settings: Mapping[str, object],
        manifest: Mapping[str, object],
        catalog: Mapping[str, object] | None,
        *,
        force_image_format: ImageFormat | None = None,
    ) -> PreparedPipelineBinding:
        if force_image_format not in (None, "ORIGINAL", "JPEG", "PNG", "WEBP"):
            raise ValueError("force_image_format must be ORIGINAL, JPEG, PNG, WEBP, or None")
        return cls(
            plugin_id,
            snapshot(plugin_config),
            snapshot(app_settings),
            snapshot(manifest),
            snapshot(catalog) if catalog is not None else None,
            config.output.image_format,
            config.media.input_validation,
            config.media.content_type_mismatch,
            config.media.max_image_pixels,
            frozenset(
                processor for processor, sites in config.image_processors.transport_metadata_access.items()
                if plugin_id in sites
            ),
            force_image_format,
        )


def prepare_runtime_binding(
    config: AppConfig,
    registry: PluginRuntime,
    record: PluginRecord,
    overrides: PluginConfigOverrides | None,
    force_image_format: ImageFormat | None,
) -> PreparedPipelineBinding:
    """Compatibility adapter; never used for per-image execution."""
    from ..plugins.runtime import safe_app_settings

    return PreparedPipelineBinding.prepare(
        config,
        record.id,
        registry.effective_config(record, overrides),
        safe_app_settings(config),
        record.manifest.value,
        record.catalog.as_json() if record.catalog is not None else None,
        force_image_format=force_image_format,
    )
