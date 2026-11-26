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
    input_validation: str
    content_type_mismatch: str
    max_image_pixels: int | None
    metadata_processors: frozenset[str]
    image_format: ImageFormat | None
    image_quality: int | None

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
        image_format: ImageFormat | None = None,
        image_quality: int | None = None,
    ) -> PreparedPipelineBinding:
        from ..application.requests import resolve_image_output

        resolved_format = resolve_image_output(config, image_format, image_quality)
        return cls(
            plugin_id,
            snapshot(plugin_config),
            snapshot(app_settings),
            snapshot(manifest),
            snapshot(catalog) if catalog is not None else None,
            config.media.input_validation,
            config.media.content_type_mismatch,
            config.media.max_image_pixels,
            frozenset(
                processor
                for processor, sites in config.image_processors.transport_metadata_access.items()
                if plugin_id in sites
            ),
            resolved_format,
            image_quality,
        )


def prepare_runtime_binding(
    config: AppConfig,
    registry: PluginRuntime,
    record: PluginRecord,
    overrides: PluginConfigOverrides | None,
    image_format: ImageFormat | None,
    image_quality: int | None = None,
) -> PreparedPipelineBinding:
    """Constructor adapter; never used for per-image execution."""
    from ..plugins.runtime import safe_app_settings

    return PreparedPipelineBinding.prepare(
        config,
        record.id,
        registry.effective_config(record, overrides),
        safe_app_settings(config),
        record.manifest.value,
        record.catalog.as_json() if record.catalog is not None else None,
        image_format=image_format,
        image_quality=image_quality,
    )
