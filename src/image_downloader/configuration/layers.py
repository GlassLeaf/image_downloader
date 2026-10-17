"""Validated layered YAML configuration resolution and provenance."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

import yaml
from pydantic import ValidationError

from image_downloader.configuration.merging import deep_merge
from image_downloader.configuration.normalization import (
    _apply_cleanup,
    _LayerCleanup,
    _sanitize_user_layer,
)
from image_downloader.configuration.provenance import _flatten, _merge_layer

from ..diagnostics import Diagnostic, configuration_error, configuration_source, diagnostic_for, validation_diagnostic
from ..exceptions import ConfigurationError
from ..storage.path_safety import canonical_path, existing_directory, existing_regular_file
from .hosts import normalize_host, registrable_domain, site_file_name
from .models import DEFAULT_CONFIG, AppConfig
from .paths import default_data_root, default_plugin_root

LayerStatus = Literal["applied", "missing", "not_applicable"]


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
        kinds: Mapping[str, Literal["package_defaults", "fixed_user", "explicit"]] = {
            "defaults": "package_defaults",
            "user": "fixed_user",
            "explicit": "explicit",
        }
        return kinds[self.source]


def validate_config(value: Mapping[str, Any] | AppConfig) -> AppConfig:
    if isinstance(value, AppConfig):
        return value
    try:
        return AppConfig.model_validate(value)
    except ValidationError as exc:
        issue = exc.errors()[0]
        raise validation_diagnostic(issue, model=AppConfig) from exc


def _read_yaml_document(resolved: Path, original: Path) -> tuple[dict[str, Any], bytes]:
    try:
        source = resolved.read_bytes()
        decoded = yaml.safe_load(source.decode("utf-8"))
        result = {} if decoded is None else decoded
    except yaml.YAMLError as exc:
        details: dict[str, object] = {"source": configuration_source(original)}
        mark = getattr(exc, "problem_mark", None)
        if mark is not None:
            details.update(line=mark.line + 1, column=mark.column + 1)
        raise configuration_error("configuration YAML syntax is invalid", "configuration_syntax", **details) from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise configuration_error(
            "configuration file cannot be read as UTF-8", "configuration_read", source=configuration_source(original)
        ) from exc
    if not isinstance(result, dict):
        raise configuration_error(
            "configuration root must be a mapping",
            "configuration_value",
            field="configuration",
            source=configuration_source(original),
        )
    return result, source


def _read_yaml(resolved: Path, original: Path) -> dict[str, Any]:
    return _read_yaml_document(resolved, original)[0]


def _resolved_yaml_path(path: Path, *, required: bool, config_root: Path | None) -> Path | None:
    try:
        resolved = existing_regular_file(path, "configuration file", required=False)
    except ConfigurationError as exc:
        exc._core_diagnostic = diagnostic_for(
            configuration_error(
                "configuration file requires an absolute safe regular file path",
                "configuration_read",
                source=configuration_source(path),
            )
        )
        raise
    if resolved is None and required:
        raise configuration_error(
            "configuration file not found", "configuration_missing", source=configuration_source(path)
        )
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
        raise configuration_error(
            "profile is only allowed in the main app.yaml",
            "configuration_layer",
            field="profile",
            source=configuration_source(path, role),
        )
    if role in {"profile_app", "site"}:
        forbidden_keys = {"storage", "plugins"}
        if role == "site":
            forbidden_keys.add("security")
        forbidden = sorted(forbidden_keys.intersection(raw))
        if forbidden:
            raise configuration_error(
                f"bootstrap settings ({', '.join(forbidden)}) are not allowed in this configuration layer",
                "configuration_layer",
                field=", ".join(forbidden),
                source=configuration_source(path, role),
            )


def _validate_layer_values(raw: Mapping[str, Any], path: Path, role: str) -> None:
    _validate_layer_role(raw, path, role)
    try:
        AppConfig.model_validate(deep_merge(DEFAULT_CONFIG.model_dump(by_alias=True, warnings=False), raw))
    except ValidationError as exc:
        issue = exc.errors()[0]
        error = validation_diagnostic(issue, source=configuration_source(path, role), model=AppConfig)
        error.args = (f"invalid configuration in {role} layer {configuration_source(path)}: {error}",)
        raise error from exc


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
    source: bytes | None = None
    try:
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
    except ConfigurationError as exc:
        diagnostic = diagnostic_for(exc)
        if diagnostic is not None:
            details = dict(diagnostic.details)
            details["source"] = configuration_source(path, role)
            field = details.get("field")
            if source is not None and isinstance(field, str):
                details.update(_field_mark(source, field))
            exc._core_diagnostic = Diagnostic(diagnostic.message, MappingProxyType(details))
        raise


def _field_mark(source: bytes, field: str) -> dict[str, int]:
    """Retain a field's location, never its value or YAML excerpt."""
    node = yaml.compose(source.decode("utf-8"))
    for name in field.split("."):
        if not isinstance(node, yaml.MappingNode):
            break
        child = next((value for key, value in node.value if key.value == name), None)
        if child is None:
            break
        node = child
    if node is None:
        return {}
    return {"line": node.start_mark.line + 1, "column": node.start_mark.column + 1}


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
