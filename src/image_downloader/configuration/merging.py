"""Detached recursive merge of configuration mappings."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        result[key] = (
            deep_merge(result[key], value)
            if isinstance(result.get(key), Mapping) and isinstance(value, Mapping)
            else value
        )
    return result
