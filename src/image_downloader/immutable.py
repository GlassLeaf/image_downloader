"""Recursive immutable and mutable conversions for JSON-shaped values."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import cast

from ._numeric import _is_finite


def freeze_plugin_data(value: Mapping[str, object]) -> Mapping[str, object]:
    """Copy strict JSON-shaped plugin data without accepting cycles or coercing keys."""
    if not isinstance(value, Mapping):
        raise TypeError("plugin data must be a mapping")
    ancestors: set[int] = set()

    def freeze(item: object) -> object:
        if item is None or isinstance(item, (str, bool, int)):
            return item
        if isinstance(item, float):
            if not _is_finite(item):
                raise ValueError("plugin data must contain finite numbers")
            return item
        if not isinstance(item, (Mapping, list, tuple)):
            raise TypeError("plugin data must contain JSON values")
        if id(item) in ancestors:
            raise ValueError("plugin data must not contain cycles")
        ancestors.add(id(item))
        try:
            if isinstance(item, Mapping):
                if any(not isinstance(key, str) for key in item):
                    raise TypeError("plugin data keys must be strings")
                return MappingProxyType({key: freeze(child) for key, child in item.items()})
            return tuple(freeze(child) for child in item)
        finally:
            ancestors.remove(id(item))

    # The root was checked above; nested mappings use the same strict policy.
    return cast(Mapping[str, object], freeze(value))


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
