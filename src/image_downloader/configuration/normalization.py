"""Plan and safely apply removal of obsolete configuration keys."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from functools import cache
from io import StringIO
from pathlib import Path
from types import MappingProxyType, UnionType
from typing import Annotated, Any, Literal, Protocol, Union, cast, get_args, get_origin

from pydantic import BaseModel
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from ..exceptions import ConfigurationError
from .models import AppConfig


class _YamlLineBreak(Protocol):
    # ruamel.yaml supports string line breaks but infers this attribute as None.
    line_break: str | None


@dataclass(frozen=True)
class _ValueShape:
    """The subset of a Pydantic annotation needed to prune mapping keys."""

    properties: Mapping[str, _ValueShape] | None = None
    mapping_value: _ValueShape | None = None
    mapping_literals: frozenset[object] | None = None
    sequence_value: _ValueShape | None = None


@dataclass(frozen=True)
class _LayerCleanup:
    """A validated, source-checked set of mapping entries to remove."""

    path: Path
    source: bytes
    removals: tuple[tuple[object, ...], ...]


_REMOVED_KEYS = {
    ("network", "max_retries"): "network.max_attempts",
    ("network", "max_retry_wait_seconds"): "network.retry_max_delay_seconds",
    ("network", "max_auth_retries"): "network.auth_refresh_attempts",
    ("network", "max_concurrency"): "network.request_concurrency",
    ("network", "host_max_concurrency"): "network.origin_request_concurrency",
    ("network", "site_max_concurrency"): "network.registrable_domain_request_concurrency",
    ("network", "request_interval_seconds"): "network.global_request_interval_seconds",
    ("network", "max_connections"): "network.pool_max_connections",
    ("network", "max_keepalive_connections"): "network.pool_max_idle_connections",
    ("network", "max_chapter_concurrency"): "download.chapter_concurrency",
    ("continue_on_error",): "download.continue_on_image_error",
    ("allow_empty_manifest",): "download.allow_empty_chapter_manifest",
}


def _annotation_shape(annotation: object) -> _ValueShape:
    """Describe nested models and mappings without changing scalar values."""
    origin = get_origin(annotation)
    if origin is Annotated:
        return _annotation_shape(get_args(annotation)[0])
    if origin in {Union, UnionType}:
        values = tuple(value for value in get_args(annotation) if value is not type(None))
        return _annotation_shape(values[0]) if len(values) == 1 else _ValueShape()
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _model_shape(annotation)
    if origin is Mapping:
        key, value = get_args(annotation)
        key_origin = get_origin(key)
        literals = frozenset(get_args(key)) if key_origin is Literal else None
        return _ValueShape(mapping_value=_annotation_shape(value), mapping_literals=literals)
    if origin in {list, tuple}:
        values = get_args(annotation)
        if values:
            return _ValueShape(sequence_value=_annotation_shape(values[0]))
    return _ValueShape()


@cache
def _model_shape(model: type[BaseModel]) -> _ValueShape:
    return _ValueShape(
        properties=MappingProxyType(
            {field.alias or name: _annotation_shape(field.annotation) for name, field in model.model_fields.items()}
        )
    )


_APP_CONFIG_SHAPE = _model_shape(AppConfig)


def _prune_obsolete_and_unknown(
    value: object,
    shape: _ValueShape,
    prefix: tuple[object, ...] = (),
) -> tuple[object, tuple[tuple[object, ...], ...]]:
    """Return a detached value with removable keys excluded and their paths."""
    if shape.properties is not None and isinstance(value, Mapping):
        sanitized: dict[object, object] = {}
        removals: list[tuple[object, ...]] = []
        for key, item in value.items():
            path = prefix + (key,)
            child = shape.properties.get(key) if isinstance(key, str) else None
            if path in _REMOVED_KEYS or child is None:
                removals.append(path)
                continue
            clean, nested = _prune_obsolete_and_unknown(item, child, path)
            sanitized[key] = clean
            removals.extend(nested)
        return sanitized, tuple(removals)
    if shape.mapping_value is not None and isinstance(value, Mapping):
        sanitized = {}
        removals = []
        for key, item in value.items():
            path = prefix + (key,)
            if shape.mapping_literals is not None and key not in shape.mapping_literals:
                removals.append(path)
                continue
            clean, nested = _prune_obsolete_and_unknown(item, shape.mapping_value, path)
            sanitized[key] = clean
            removals.extend(nested)
        return sanitized, tuple(removals)
    if shape.sequence_value is not None and isinstance(value, (list, tuple)):
        sanitized_items: list[object] = []
        removals = []
        for index, item in enumerate(value):
            clean, nested = _prune_obsolete_and_unknown(item, shape.sequence_value, prefix + (index,))
            sanitized_items.append(clean)
            removals.extend(nested)
        return sanitized_items, tuple(removals)
    return value, ()


def _sanitize_user_layer(raw: Mapping[str, Any]) -> tuple[dict[str, Any], tuple[tuple[object, ...], ...]]:
    clean, removals = _prune_obsolete_and_unknown(raw, _APP_CONFIG_SHAPE)
    assert isinstance(clean, dict)
    return clean, removals


def _apply_cleanup(plan: _LayerCleanup) -> None:
    """Remove validated keys while retaining the rest of the YAML document."""
    try:
        if plan.path.read_bytes() != plan.source:
            raise ConfigurationError(f"configuration changed while normalizing: {plan.path}")
        document_yaml = YAML(typ="rt")
        document_yaml.preserve_quotes = True
        if b"\r\n" in plan.source:
            cast(_YamlLineBreak, document_yaml).line_break = "\r\n"
        document = document_yaml.load(plan.source.decode("utf-8"))
        if not isinstance(document, Mapping):
            raise ConfigurationError(f"configuration root must be a mapping: {plan.path}")
        for removal in plan.removals:
            current: object = document
            for key in removal[:-1]:
                if not isinstance(current, Mapping) or key not in current:
                    raise ConfigurationError(f"configuration changed while normalizing: {plan.path}")
                current = current[key]
            if not isinstance(current, MutableMapping) or removal[-1] not in current:
                raise ConfigurationError(f"configuration changed while normalizing: {plan.path}")
            del current[removal[-1]]
        rendered = StringIO()
        document_yaml.dump(document, rendered)
        _atomic_replace(plan.path, plan.source, rendered.getvalue().encode("utf-8"))
    except ConfigurationError:
        raise
    except (OSError, UnicodeDecodeError, ValueError, YAMLError) as exc:
        raise ConfigurationError(f"could not normalize configuration: {plan.path}: {exc}") from exc


def _atomic_replace(path: Path, expected: bytes, content: bytes) -> None:
    """Atomically replace *path* only when it has not changed since loading."""
    descriptor: int | None = None
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if path.read_bytes() != expected:
            raise ConfigurationError(f"configuration changed while normalizing: {path}")
        os.replace(temporary, path)
    except ConfigurationError:
        raise
    except OSError as exc:
        raise ConfigurationError(f"could not normalize configuration: {path}: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
