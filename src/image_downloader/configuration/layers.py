"""Validated layered YAML configuration resolution and provenance."""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from io import StringIO
from pathlib import Path
from types import MappingProxyType, UnionType
from typing import Annotated, Any, Literal, Union, get_args, get_origin

import yaml
from pydantic import BaseModel, ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from ..exceptions import ConfigurationError
from ..storage.path_safety import canonical_path, existing_directory, existing_regular_file
from .hosts import normalize_host, registrable_domain, site_file_name
from .models import DEFAULT_CONFIG, AppConfig
from .paths import default_data_root, default_plugin_root

LayerStatus = Literal["applied", "missing", "not_applicable"]


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


@dataclass(frozen=True)
class ConfigurationLayer:
    """One candidate configuration layer considered by the resolver."""

    role: str
    path: Path | None
    status: LayerStatus


@dataclass(frozen=True)
class ResolvedApplicationConfig:
    """An effective configuration plus the facts needed to explain it."""

    config: AppConfig
    main_config_path: Path | None
    config_root: Path
    source: Literal["defaults", "user", "explicit"]
    selected_profile: str
    layers: tuple[ConfigurationLayer, ...]
    origins: Mapping[str, str]

    @property
    def main_config_kind(self) -> Literal["package_defaults", "fixed_user", "explicit"]:
        """Classify main-config selection independently from its filesystem path."""
        return {
            "defaults": "package_defaults",
            "user": "fixed_user",
            "explicit": "explicit",
        }[self.source]


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
            {
                field.alias or name: _annotation_shape(field.annotation)
                for name, field in model.model_fields.items()
            }
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


def _dotted(path: tuple[object, ...]) -> str:
    return ".".join(str(item) for item in path) or "configuration"


def validate_config(value: Mapping[str, Any] | AppConfig) -> AppConfig:
    if isinstance(value, AppConfig):
        return value
    try:
        return AppConfig.model_validate(value)
    except ValidationError as exc:
        issue = exc.errors()[0]
        raise ConfigurationError(f"invalid configuration: {_dotted(tuple(issue['loc']))}: {issue['msg']}") from exc


def deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        result[key] = (
            deep_merge(result[key], value)
            if isinstance(result.get(key), Mapping) and isinstance(value, Mapping)
            else value
        )
    return result


def _read_yaml_document(resolved: Path, original: Path) -> tuple[dict[str, Any], bytes]:
    try:
        source = resolved.read_bytes()
        result = yaml.safe_load(source.decode("utf-8")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"could not read configuration: {original}: {exc}") from exc
    if not isinstance(result, dict):
        raise ConfigurationError(f"configuration root must be a mapping: {original}")
    return result, source


def _read_yaml(resolved: Path, original: Path) -> dict[str, Any]:
    return _read_yaml_document(resolved, original)[0]


def _resolved_yaml_path(path: Path, *, required: bool, config_root: Path | None) -> Path | None:
    resolved = existing_regular_file(path, "configuration file", required=required)
    if resolved is None:
        return None
    if config_root is not None:
        root = existing_directory(config_root, "configuration root", required=True)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ConfigurationError(f"configuration file escapes configuration root: {path}") from exc
    return resolved


def load_yaml(path: Path, *, required: bool = False, config_root: Path | None = None) -> dict[str, Any]:
    """Load an optional YAML layer without following untrusted path redirects."""
    resolved = _resolved_yaml_path(path, required=required, config_root=config_root)
    return {} if resolved is None else _read_yaml(resolved, path)


def _site_layers(root: Path, profile: str, host: str | None) -> tuple[Path, ...]:
    layers: list[Path] = [root / "sites" / "global.yaml", root / "profiles" / profile / "sites" / "global.yaml"]
    if not host:
        return tuple(layers)
    normalized, is_ip = normalize_host(host)
    if is_ip:
        names: tuple[str, ...] = (normalized,)
    else:
        labels, parent = normalized.split("."), registrable_domain(normalized)
        start = len(labels) - len(parent.split("."))
        names = tuple(".".join(labels[index:]) for index in range(start, -1, -1))
    for name in names:
        filename = site_file_name(name)
        layers.extend((root / "sites" / filename, root / "profiles" / profile / "sites" / filename))
    return tuple(layers)


def _validate_layer_role(raw: Mapping[str, Any], path: Path, role: str) -> None:
    if role not in {"app", "baseline"} and "profile" in raw:
        raise ConfigurationError(f"profile is only allowed in the main app.yaml: {path}")
    if role in {"profile_app", "site"}:
        forbidden_keys = {"storage", "plugins"}
        if role == "site":
            forbidden_keys.add("security")
        forbidden = sorted(forbidden_keys.intersection(raw))
        if forbidden:
            raise ConfigurationError(
                f"bootstrap settings ({', '.join(forbidden)}) are not allowed in this configuration layer: {path}"
            )


def _validate_layer_values(raw: Mapping[str, Any], path: Path, role: str) -> None:
    _validate_layer_role(raw, path, role)
    try:
        AppConfig.model_validate(deep_merge(DEFAULT_CONFIG.model_dump(by_alias=True, warnings=False), raw))
    except ValidationError as exc:
        issue = exc.errors()[0]
        raise ConfigurationError(
            f"invalid configuration in {role} layer {path}: {_dotted(tuple(issue['loc']))}: {issue['msg']}"
        ) from exc


def _apply_cleanup(plan: _LayerCleanup) -> None:
    """Remove validated keys while retaining the rest of the YAML document."""
    try:
        if plan.path.read_bytes() != plan.source:
            raise ConfigurationError(f"configuration changed while normalizing: {plan.path}")
        document_yaml = YAML(typ="rt")
        document_yaml.preserve_quotes = True
        if b"\r\n" in plan.source:
            document_yaml.line_break = "\r\n"
        document = document_yaml.load(plan.source.decode("utf-8"))
        if not isinstance(document, Mapping):
            raise ConfigurationError(f"configuration root must be a mapping: {plan.path}")
        for removal in plan.removals:
            current: object = document
            for key in removal[:-1]:
                if not isinstance(current, Mapping) or key not in current:
                    raise ConfigurationError(f"configuration changed while normalizing: {plan.path}")
                current = current[key]
            if not isinstance(current, Mapping) or removal[-1] not in current:
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


def _reject_profile_override(override: Mapping[str, Any], source: str) -> None:
    """Keep profile selection ahead of every profile-dependent configuration layer."""
    if "profile" in override:
        raise ConfigurationError(
            f"profile cannot be overridden by {source}; select it with main app.yaml "
            "or load_application_config(profile=...)"
        )


def _read_layer(
    path: Path,
    *,
    role: str,
    config_root: Path | None,
    cleanups: list[_LayerCleanup] | None = None,
    required: bool = False,
) -> tuple[dict[str, Any], ConfigurationLayer]:
    resolved = _resolved_yaml_path(path, required=required, config_root=config_root)
    if resolved is None:
        return {}, ConfigurationLayer(role, path, "missing")
    raw, source = _read_yaml_document(resolved, path)
    if role in {"app", "profile_app", "site"}:
        raw, removals = _sanitize_user_layer(raw)
        if cleanups is not None and removals:
            cleanups.append(_LayerCleanup(resolved, source, removals))
    _validate_layer_values(raw, path, role)
    return raw, ConfigurationLayer(role, resolved, "applied")


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


def _materialize_automatic_roots(data: dict[str, Any], origins: dict[str, str]) -> dict[str, Any]:
    storage = dict(data["storage"])
    plugins = dict(data["plugins"])
    if storage["data_root"] is None:
        storage["data_root"] = str(default_data_root())
        origins["storage.data_root"] = "platform default (automatic data root)"
    if plugins["root"] is None:
        plugins["root"] = str(default_plugin_root())
        origins["plugins.root"] = "platform default (automatic plugin root)"
    data["storage"] = storage
    data["plugins"] = plugins
    return data


def resolve_application_config(
    path: Path | None,
    profile: str | None = None,
    site: str | None = None,
    *,
    require_config: bool = False,
    runtime_override: Mapping[str, Any] | None = None,
    source: Literal["defaults", "user", "explicit"] | None = None,
    rewrite_user_layers: bool = False,
) -> ResolvedApplicationConfig:
    """Resolve layers and optionally remove obsolete user-layer keys from disk."""
    if runtime_override is not None:
        _reject_profile_override(runtime_override, "runtime_override")
        _validate_layer_values(runtime_override, Path("<runtime override>"), "runtime")

    cleanups: list[_LayerCleanup] | None = [] if rewrite_user_layers else None
    bundled = Path(__file__).parents[1] / "app.yaml"
    baseline, baseline_layer = _read_layer(bundled, role="baseline", config_root=None, required=True)
    layers: list[ConfigurationLayer] = [ConfigurationLayer("schema_default", None, "applied"), baseline_layer]
    data = DEFAULT_CONFIG.model_dump(by_alias=True, warnings=False)
    origins = {".".join(item): "schema default" for item in _flatten(data) if item}
    data = _merge_layer(data, origins, baseline, str(bundled))

    main_path: Path | None = None
    root = bundled.parent
    if path is not None:
        main_path = canonical_path(Path(path), "configuration path")
        root = main_path.parent
        existing_directory(root, "configuration root", required=require_config)
        if main_path == bundled.resolve():
            # The package policy file is already the baseline; selecting it
            # explicitly must not turn it into a second, higher-priority layer.
            layers.append(ConfigurationLayer("app", main_path, "not_applicable"))
        else:
            main, main_layer = _read_layer(
                main_path,
                role="app",
                config_root=root,
                cleanups=cleanups,
                required=require_config,
            )
            layers.append(main_layer)
            data = _merge_layer(data, origins, main, str(main_path))
    else:
        layers.append(ConfigurationLayer("app", None, "not_applicable"))

    raw_profile = data.get("profile", {})
    if not isinstance(raw_profile, Mapping):
        raise ConfigurationError("invalid configuration: profile")
    configured_default = raw_profile.get("default", DEFAULT_CONFIG.profile.default)
    selected_value = profile if profile is not None else configured_default
    if not isinstance(selected_value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", selected_value):
        raise ConfigurationError("invalid configuration: profile.default")
    selected = selected_value
    data["profile"] = deep_merge(dict(data.get("profile", {})), {"default": selected})
    origins["profile.default"] = (
        "CLI --profile" if profile is not None else origins.get("profile.default", "schema default")
    )

    profile_app = root / "profiles" / selected / "app.yaml"
    explicit_alternate_profile = profile is not None and selected != configured_default
    overlay, layer = _read_layer(
        profile_app,
        role="profile_app",
        config_root=root,
        cleanups=cleanups,
        required=explicit_alternate_profile,
    )
    layers.append(layer)
    data = _merge_layer(data, origins, overlay, str(profile_app))
    for layer_path in _site_layers(root, selected, site):
        overlay, layer = _read_layer(
            layer_path,
            role="site",
            config_root=root,
            cleanups=cleanups,
            required=False,
        )
        layers.append(layer)
        data = _merge_layer(data, origins, overlay, str(layer_path))
    data["profile"] = deep_merge(dict(data.get("profile", {})), {"default": selected})
    if runtime_override:
        data = _merge_layer(data, origins, runtime_override, "runtime override")
    data = _materialize_automatic_roots(data, origins)
    config = validate_config(data)
    if cleanups:
        for cleanup in cleanups:
            _apply_cleanup(cleanup)
    resolved_source = source or ("defaults" if path is None else "explicit")
    return ResolvedApplicationConfig(
        config=config,
        main_config_path=main_path,
        config_root=root,
        source=resolved_source,
        selected_profile=selected,
        layers=tuple(layers),
        origins=MappingProxyType(dict(sorted(origins.items()))),
    )


def load_application_config(
    path: Path,
    profile: str | None = None,
    site: str | None = None,
    *,
    require_config: bool = False,
    runtime_override: Mapping[str, Any] | None = None,
    rewrite_user_layers: bool = False,
) -> AppConfig:
    """Load layered configuration while preserving the established AppConfig return value."""
    return resolve_application_config(
        path,
        profile,
        site,
        require_config=require_config,
        runtime_override=runtime_override,
        rewrite_user_layers=rewrite_user_layers,
    ).config


def apply_overrides(config: AppConfig, override: Mapping[str, Any]) -> AppConfig:
    _reject_profile_override(override, "apply_overrides()")
    _validate_layer_values(override, Path("<runtime override>"), "runtime")
    return validate_config(deep_merge(config.model_dump(by_alias=True, warnings=False), override))
