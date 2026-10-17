"""Track the origin of each effective configuration value."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .merging import deep_merge


def _dotted(path: tuple[object, ...]) -> str:
    return ".".join(str(item) for item in path) or "configuration"


def _flatten(value: Any, prefix: tuple[str, ...] = ()) -> tuple[tuple[str, ...], ...]:
    if isinstance(value, Mapping):
        if not value:
            return (prefix,)
        return tuple(item for key, child in value.items() for item in _flatten(child, prefix + (str(key),)))
    return (prefix,)


def _record_origins(origins: dict[str, str], value: Mapping[str, Any], label: str) -> None:
    for path in _flatten(value):
        dotted = _dotted(path)
        for current in tuple(origins):
            if current == dotted or current.startswith(f"{dotted}."):
                del origins[current]
        origins[dotted] = label


def _merge_layer(
    data: Mapping[str, Any], origins: dict[str, str], raw: Mapping[str, Any], label: str
) -> dict[str, Any]:
    if not raw:
        return dict(data)
    _record_origins(origins, raw, label)
    return deep_merge(data, raw)
