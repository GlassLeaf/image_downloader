"""Layered YAML configuration loading and overrides."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from ..exceptions import ConfigurationError
from ..storage.path_safety import canonical_path, existing_directory, existing_regular_file
from .hosts import normalize_host, registrable_domain, site_file_name
from .models import DEFAULT_CONFIG, AppConfig


def validate_config(value: Mapping[str, Any] | AppConfig) -> AppConfig:
    if isinstance(value, AppConfig):
        return value
    try:
        return AppConfig.model_validate(value)
    except ValidationError as exc:
        issue = exc.errors()[0]
        raise ConfigurationError(f"invalid configuration: {issue['loc']}: {issue['msg']}") from exc


def deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        result[key] = (
            deep_merge(result[key], value)
            if isinstance(result.get(key), Mapping) and isinstance(value, Mapping)
            else value
        )
    return result


def load_yaml(path: Path, *, required: bool = False, config_root: Path | None = None) -> dict[str, Any]:
    """Load an optional YAML layer without following untrusted path redirects."""
    resolved = existing_regular_file(path, "configuration file", required=required)
    if resolved is None:
        return {}
    if config_root is not None:
        root = existing_directory(config_root, "configuration root", required=True)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ConfigurationError(f"configuration file escapes configuration root: {path}") from exc
    try:
        result = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"could not read configuration: {path}: {exc}") from exc
    if not isinstance(result, dict):
        raise ConfigurationError(f"configuration root must be a mapping: {path}")
    return result


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


_BOOTSTRAP_KEYS = frozenset({"storage", "plugins", "security"})


def _validate_layer(raw: Mapping[str, Any], path: Path, role: str) -> None:
    if role != "app" and "profile" in raw:
        raise ConfigurationError(f"profile is only allowed in the main app.yaml: {path}")
    if role != "app":
        forbidden_keys = {"storage", "plugins"}
        if role == "site":
            forbidden_keys.add("security")
        forbidden = sorted(forbidden_keys.intersection(raw))
        if forbidden:
            raise ConfigurationError(
                f"bootstrap settings ({', '.join(forbidden)}) are not allowed in this configuration layer: {path}"
            )


def _reject_profile_override(override: Mapping[str, Any], source: str) -> None:
    """Keep profile selection ahead of every profile-dependent configuration layer."""
    if "profile" in override:
        raise ConfigurationError(
            f"profile cannot be overridden by {source}; select it with main "
            "app.yaml or load_application_config(profile=...)"
        )


def load_application_config(
    path: Path,
    profile: str | None = None,
    site: str | None = None,
    *,
    require_config: bool = False,
    baseline_path: Path | None = None,
    runtime_override: Mapping[str, Any] | None = None,
) -> AppConfig:
    """Load v3 layers, selecting a profile before reading its overlays."""
    if runtime_override is not None:
        _reject_profile_override(runtime_override, "runtime_override")
    raw_path = Path(path)
    path = canonical_path(raw_path, "configuration path")
    root = path.parent
    existing_directory(root, "configuration root", required=require_config)
    bundled = Path(__file__).parents[1] / "app.yaml"
    baseline = load_yaml(bundled, required=True)
    if baseline_path is not None:
        supplied = canonical_path(baseline_path, "baseline configuration path")
        baseline = deep_merge(baseline, load_yaml(supplied, required=True))
    _validate_layer(baseline, bundled, "app")
    main = load_yaml(raw_path, required=require_config, config_root=root)
    _validate_layer(main, path, "app")
    combined_main = deep_merge(baseline, main)
    raw_profile = combined_main.get("profile", {})
    if not isinstance(raw_profile, Mapping):
        raise ConfigurationError("invalid configuration: profile")
    configured_default = raw_profile.get("default", DEFAULT_CONFIG.profile.default)
    selected_value = profile if profile is not None else configured_default
    if not isinstance(selected_value, str):
        raise ConfigurationError("invalid configuration: profile.default")
    selected = selected_value
    if not re.fullmatch(r"[A-Za-z0-9_-]+", selected):
        raise ConfigurationError("invalid configuration: profile.default")
    data = deep_merge(DEFAULT_CONFIG.model_dump(by_alias=True, warnings=False), combined_main)
    data["profile"] = deep_merge(dict(data.get("profile", {})), {"default": selected})
    profile_app = root / "profiles" / selected / "app.yaml"
    explicit_alternate_profile = profile is not None and selected != configured_default
    overlay = load_yaml(profile_app, required=explicit_alternate_profile, config_root=root)
    _validate_layer(overlay, profile_app, "profile_app")
    data = deep_merge(data, overlay)
    for layer in _site_layers(root, selected, site):
        overlay = load_yaml(layer, config_root=root)
        _validate_layer(overlay, layer, "site")
        data = deep_merge(data, overlay)
    data["profile"] = deep_merge(dict(data.get("profile", {})), {"default": selected})
    if runtime_override:
        data = deep_merge(data, runtime_override)
    return validate_config(data)


def apply_overrides(config: AppConfig, override: Mapping[str, Any]) -> AppConfig:
    _reject_profile_override(override, "apply_overrides()")
    return validate_config(deep_merge(config.model_dump(by_alias=True, warnings=False), override))
