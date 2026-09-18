"""Recursive immutable and mutable conversions for JSON-shaped values."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType


def freeze_json(value: object) -> object:
    """Return a detached, recursively immutable representation of *value*.

    Mapping keys are normalized to strings, sequences retain their order as
    tuples, and unordered collections become frozensets. Scalar values and
    application value objects are returned unchanged.
    """
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze_json(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(freeze_json(item) for item in value)
    return value


def thaw_json(value: object) -> object:
    """Return detached dictionaries and lists suitable for a JSON boundary."""
    if isinstance(value, Mapping):
        return {str(key): thaw_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw_json(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [thaw_json(item) for item in value]
    return value
