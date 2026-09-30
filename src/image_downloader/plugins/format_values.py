"""Collection of stable output-format values from operation plugins."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from ..immutable import freeze_json
from ..ports import PluginFormatContext
from .lifecycle import PluginRecord
from .plugin_invoker import PluginInvoker


@dataclass(frozen=True, slots=True)
class PluginFormatValueParticipant:
    """One operation plugin eligible to provide stable output-format values."""

    record: PluginRecord
    instance: object
    config: Mapping[str, object]
    app_settings: Mapping[str, object]
    invoker: PluginInvoker


def collect_plugin_format_values(
    operation_url: str,
    participants: Sequence[PluginFormatValueParticipant],
) -> Mapping[str, Mapping[str, str]]:
    """Collect and freeze one stable mapping for every operation participant."""
    values: dict[str, dict[str, str]] = {}
    for participant in participants:
        plugin_id = participant.record.id
        if plugin_id in values:
            raise RuntimeError(f"duplicate output-format participant: {plugin_id}")
        catalog = participant.record.catalog.as_json() if participant.record.catalog is not None else None
        context = PluginFormatContext(
            plugin_id,
            participant.record.kind,
            operation_url,
            participant.config,
            participant.app_settings,
            participant.record.manifest.value,
            catalog,
        )
        values[plugin_id] = dict(participant.invoker.output_format_values(participant.instance, context))
    return cast(Mapping[str, Mapping[str, str]], freeze_json(values))
