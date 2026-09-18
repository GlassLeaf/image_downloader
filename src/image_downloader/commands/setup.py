"""CLI configuration preparation and root setup."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

import yaml
from platformdirs import PlatformDirs

from ..configuration.layers import apply_overrides, load_application_config
from ..configuration.models import AppConfig
from ..configuration.paths import plugin_root as configured_plugin_root
from ..configuration.paths import resolve_paths
from ..exceptions import ConfigurationError
from ..storage.path_safety import canonical_path, existing_directory, existing_regular_file


def _bundled_config_path() -> Path:
    return Path(__file__).parents[1] / "app.yaml"


def _user_config_path() -> Path:
    return PlatformDirs("image-downloader", appauthor=False).user_config_path / "conf" / "app.yaml"


def _yaml_plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _yaml_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_yaml_plain(item) for item in value]
    return value


def _user_config_payload(data_root: Path, plugins_root: Path) -> dict[str, object]:
    payload = _yaml_plain(AppConfig().model_dump(by_alias=True, warnings=False))
    assert isinstance(payload, dict)
    payload["storage"] = {"data_root": str(data_root.resolve())}
    payload["plugins"] = {"root": str(plugins_root.resolve())}
    payload["security"] = {"plugin_verification": "strict"}
    return payload


def _user_config_yaml(data_root: Path, plugins_root: Path) -> str:
    return str(yaml.safe_dump(_user_config_payload(data_root, plugins_root), allow_unicode=True, sort_keys=False))


def _write_user_config(destination: Path, data_root: Path, plugins_root: Path) -> None:
    destination = canonical_path(destination, "configuration path")
    if existing_regular_file(destination, "configuration file", required=False) is not None:
        raise ConfigurationError(f"configuration file already exists: {destination}")
    existing_directory(destination.parent, "configuration root")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("x", encoding="utf-8") as stream:
            stream.write(_user_config_yaml(data_root, plugins_root))
    except FileExistsError as exc:
        raise ConfigurationError(f"configuration file already exists: {destination}") from exc


def _write_updated_user_config(destination: Path, data_root: Path, plugins_root: Path) -> None:
    """Create or repair the user-owned root settings without touching bundled data."""
    destination = canonical_path(destination, "configuration path")
    existing = existing_regular_file(destination, "configuration file", required=False)
    if existing is None:
        _write_user_config(destination, data_root, plugins_root)
        return
    try:
        payload = yaml.safe_load(existing.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"could not read configuration: {destination}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise ConfigurationError(f"configuration root must be a mapping: {destination}")
    value = _yaml_plain(payload)
    assert isinstance(value, dict)
    storage = value.get("storage", {})
    plugins = value.get("plugins", {})
    if not isinstance(storage, dict) or not isinstance(plugins, dict):
        raise ConfigurationError(f"invalid root settings in configuration: {destination}")
    storage["data_root"] = str(data_root)
    plugins["root"] = str(plugins_root)
    value["storage"] = storage
    value["plugins"] = plugins
    descriptor, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(yaml.safe_dump(value, allow_unicode=True, sort_keys=False))
            stream.flush()
            os.fsync(stream.fileno())
        Path(temporary).replace(destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _missing_root_names(config: AppConfig) -> tuple[str, ...]:
    values = (
        ("storage.data_root", config.storage.data_root),
        ("plugins.root", config.plugins.root),
    )
    return tuple(name for name, value in values if not isinstance(value, str) or not Path(value).is_absolute())


def _configured_root(value: str | None, label: str) -> Path | None:
    if not isinstance(value, str) or not Path(value).is_absolute():
        return None
    return existing_directory(Path(value), label)


def _missing_root_error(missing: tuple[str, ...], *, explicit: bool) -> ConfigurationError:
    options = []
    if "storage.data_root" in missing:
        options.append("--data-root ABSOLUTE_PATH")
    if "plugins.root" in missing:
        options.append("--plugin-root ABSOLUTE_PATH")
    context = "explicit --config root settings" if explicit else "root settings"
    return ConfigurationError(f"{context} must be absolute paths; pass {' '.join(options)}")


def _read_root(label: str) -> Path:
    """Prompt until a user provides a safe, absolute root directory."""
    while True:
        try:
            print(f"{label} (absolute path): ", end="", file=sys.stderr, flush=True)
            raw = input().strip()
        except EOFError as exc:
            raise ConfigurationError("root input was cancelled") from exc
        if not raw:
            print(f"{label} must not be empty", file=sys.stderr)
            continue
        try:
            return existing_directory(Path(raw), label)
        except ConfigurationError as exc:
            print(f"Invalid {label}: {exc}", file=sys.stderr)


def _confirm_root_save(destination: Path) -> bool:
    try:
        print(f"Save root settings to {destination}? [y/N] ", end="", file=sys.stderr, flush=True)
        answer = input().strip().lower()
    except EOFError as exc:
        raise ConfigurationError("root save confirmation was cancelled") from exc
    return answer in {"y", "yes"}


def _collect_root_settings(
    args: argparse.Namespace,
    config: AppConfig,
    destination: Path,
    *,
    allow_noninteractive_unsaved: bool = False,
    yes_confirms_save: bool = False,
) -> tuple[Path, Path, bool]:
    """Resolve roots from options/configuration or interactive user input."""
    data_root = _configured_root(config.storage.data_root, "storage.data_root")
    plugins_root = _configured_root(config.plugins.root, "plugins.root")
    missing = _missing_root_names(config)
    if missing:
        if args.json_output or not sys.stdin.isatty():
            raise _missing_root_error(missing, explicit=False)
        if data_root is None:
            data_root = _read_root("Data root")
        if plugins_root is None:
            plugins_root = _read_root("Plugin root")
    assert data_root is not None and plugins_root is not None
    if yes_confirms_save and args.yes:
        return data_root, plugins_root, True
    if not sys.stdin.isatty():
        if allow_noninteractive_unsaved:
            return data_root, plugins_root, False
        raise ConfigurationError("root save confirmation requires an interactive terminal")
    if args.json_output:
        raise ConfigurationError("root save confirmation is not available with --json")
    return data_root, plugins_root, _confirm_root_save(destination)


def _root_override(data_root: Path, plugins_root: Path) -> Mapping[str, object]:
    return {
        "storage": {"data_root": str(data_root)},
        "plugins": {"root": str(plugins_root)},
    }


def _plugin_root(args: argparse.Namespace, config: AppConfig) -> Path:
    root = args.plugin_root if args.plugin_root is not None else configured_plugin_root(config)
    return existing_directory(root, "--plugin-root" if args.plugin_root is not None else "plugins.root")


def _bootstrap_override(args: argparse.Namespace) -> Mapping[str, object]:
    patch: dict[str, object] = {}
    if args.data_root is not None:
        patch["storage"] = {"data_root": str(existing_directory(args.data_root, "--data-root"))}
    if args.plugin_root is not None:
        patch["plugins"] = {"root": str(existing_directory(args.plugin_root, "--plugin-root"))}
    return patch


def _runtime_overrides(args: argparse.Namespace) -> dict[str, Mapping[str, object]]:
    result: dict[str, Mapping[str, object]] = {}
    for path in args.plugin_config_file:
        safe_path = existing_regular_file(path, "--plugin-config-file", required=True)
        assert safe_path is not None
        try:
            raw = json.loads(safe_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConfigurationError(f"plugin config file is invalid: {path}") from exc
        if (
            not isinstance(raw, dict)
            or set(raw) != {"plugin_id", "config"}
            or not isinstance(raw["plugin_id"], str)
            or not isinstance(raw["config"], dict)
        ):
            raise ConfigurationError(f"plugin config file has invalid schema: {path}")
        current = result.get(raw["plugin_id"], {})
        result[raw["plugin_id"]] = _deep_merge_json(current, raw["config"])
    for item in args.plugin_config:
        if "=" not in item:
            raise ConfigurationError("--plugin-config must have ID=<JSON-object> form")
        plugin_id, text = item.split("=", 1)
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigurationError("--plugin-config JSON is invalid") from exc
        if not plugin_id or not isinstance(value, dict):
            raise ConfigurationError("--plugin-config value must be a JSON object")
        result[plugin_id] = _deep_merge_json(result.get(plugin_id, {}), value)
    return result


def _deep_merge_json(base: Mapping[str, object], override: Mapping[str, object]) -> dict[str, object]:
    value = dict(base)
    for key, item in override.items():
        current = value.get(key)
        value[key] = (
            _deep_merge_json(current, item) if isinstance(current, Mapping) and isinstance(item, Mapping) else item
        )
    return value


def _app_override(args: argparse.Namespace) -> Mapping[str, object]:
    patch: dict[str, object] = {}
    if args.existing_file:
        patch["output"] = {"existing_file": args.existing_file}
    if args.image_format:
        existing_output = patch.get("output")
        output = dict(existing_output) if isinstance(existing_output, Mapping) else {}
        output["image_format"] = args.image_format
        patch["output"] = output
    if args.no_console_log or args.json_output:
        patch["logging"] = {"console": {"enabled": False}}
    return patch


def _fallback(args: argparse.Namespace) -> bool | None:
    return None if args.fallback_generic == "auto" else args.fallback_generic == "enabled"


def _config_for(
    args: argparse.Namespace,
    site: str | None,
    *,
    allow_root_setup: bool = False,
    allow_explicit_root_setup: bool = False,
    require_saved_user_config: bool = False,
) -> tuple[AppConfig, Path, Path, str]:
    if args.config is not None:
        if not args.config.is_absolute():
            raise ConfigurationError("--config must be an absolute path")
        raw_config_path, source = args.config, "explicit"
    else:
        user_config = _user_config_path()
        if existing_regular_file(user_config, "configuration file", required=False) is not None:
            raw_config_path, source = user_config, "user"
        elif allow_root_setup:
            raw_config_path, source = _bundled_config_path(), "bundled"
        else:
            raise ConfigurationError(
                f"configuration file not found: {user_config}; create it with 'config init \"{user_config}\"'"
            )
    config_path = canonical_path(raw_config_path, "configuration path")
    if existing_regular_file(raw_config_path, "configuration file", required=False) is None:
        raise ConfigurationError(
            f"configuration file not found: {config_path}; create it with 'config init \"{config_path}\"'"
        )
    config = load_application_config(
        raw_config_path,
        args.profile,
        site,
        require_config=True,
    )
    patch = dict(_bootstrap_override(args))
    patch.update(_app_override(args))
    config = apply_overrides(config, patch) if patch else config
    missing_roots = _missing_root_names(config)
    collect_roots = bool(missing_roots) or (require_saved_user_config and source == "bundled")
    if collect_roots:
        if not allow_root_setup or (source == "explicit" and not allow_explicit_root_setup):
            raise _missing_root_error(missing_roots, explicit=source == "explicit")
        if args.json_output:
            if missing_roots:
                raise _missing_root_error(missing_roots, explicit=source == "explicit")
            raise ConfigurationError("root save confirmation is not available with --json")
        save_destination = raw_config_path if source == "explicit" else _user_config_path()
        data_root, plugins_root, save_roots = _collect_root_settings(args, config, save_destination)
        if save_roots:
            _write_updated_user_config(save_destination, data_root, plugins_root)
            if source == "bundled":
                raw_config_path, source = _user_config_path(), "user"
            config_path = canonical_path(raw_config_path, "configuration path")
            config = load_application_config(
                raw_config_path,
                args.profile,
                site,
                require_config=True,
            )
            config = apply_overrides(config, patch) if patch else config
            print(f"Saved configuration roots: {save_destination}", file=sys.stderr)
        else:
            config = apply_overrides(config, _root_override(data_root, plugins_root))
    if config.security.plugin_verification == "off" and not args.allow_unverified_plugins:
        raise ConfigurationError("plugin verification 'off' requires --allow-unverified-plugins for every run")
    paths = resolve_paths(config)
    effective_plugin_root = _plugin_root(args, config)
    if source == "user":
        print(f"Using user configuration: {config_path}", file=sys.stderr)
        print(f"Downloads: {paths['downloads']}", file=sys.stderr)
        print(f"Plugin root: {effective_plugin_root}", file=sys.stderr)
        print(f"Plugin verification: {config.security.plugin_verification}", file=sys.stderr)
    return config, config_path.parent, config_path, source
