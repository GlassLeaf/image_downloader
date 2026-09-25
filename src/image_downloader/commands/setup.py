"""CLI configuration preparation and root setup."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path

import yaml

from ..configuration.layers import ResolvedApplicationConfig, resolve_application_config
from ..configuration.models import AppConfig
from ..configuration.paths import default_user_config_path, resolve_paths
from ..configuration.paths import plugin_root as configured_plugin_root
from ..exceptions import ConfigurationError
from ..immutable import thaw_json
from ..storage.path_safety import canonical_path, existing_directory, existing_regular_file


def _bundled_config_path() -> Path:
    return Path(__file__).parents[1] / "app.yaml"


def _user_config_path() -> Path:
    return default_user_config_path()


def _user_config_payload(data_root: Path | None = None, plugins_root: Path | None = None) -> dict[str, object]:
    """Persist only values explicitly chosen by the user, never a stale default snapshot."""
    payload: dict[str, object] = {}
    if data_root is not None:
        payload["storage"] = {"data_root": str(data_root.resolve())}
    if plugins_root is not None:
        payload["plugins"] = {"root": str(plugins_root.resolve())}
    return payload


def _user_config_yaml(data_root: Path | None = None, plugins_root: Path | None = None) -> str:
    template = Path(__file__).parents[1] / "config-template.yaml"
    try:
        header = template.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigurationError(f"could not read configuration template: {template}") from exc
    payload = yaml.safe_dump(_user_config_payload(data_root, plugins_root), allow_unicode=True, sort_keys=False)
    return header + str(payload)


def _initial_user_config_yaml(config: AppConfig) -> str:
    """Render a complete bundled-policy snapshot, never CLI runtime overrides."""
    payload = thaw_json(config.model_dump(by_alias=True, warnings=False))
    assert isinstance(payload, dict)
    return (
        "# Generated from the bundled app.yaml on the first state-changing run.\n"
        "# Command-line overrides are intentionally not persisted.\n"
        + yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
    )


def _write_new_user_config(destination: Path, content: str) -> None:
    destination = canonical_path(destination, "configuration path")
    if existing_regular_file(destination, "configuration file", required=False) is not None:
        raise ConfigurationError(f"configuration file already exists: {destination}")
    existing_directory(destination.parent, "configuration root")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("x", encoding="utf-8") as stream:
            stream.write(content)
    except FileExistsError as exc:
        raise ConfigurationError(f"configuration file already exists: {destination}") from exc


def _write_user_config(destination: Path, data_root: Path | None, plugins_root: Path | None) -> None:
    """Write the explicit sparse-template form used by ``config init``."""
    _write_new_user_config(destination, _user_config_yaml(data_root, plugins_root))


def _write_initial_user_config(destination: Path, config: AppConfig) -> None:
    _write_new_user_config(destination, _initial_user_config_yaml(config))


def _persist_initial_user_config(source: str) -> None:
    """Persist bundled policy before a state-changing first run.

    The snapshot deliberately excludes every command-line override.  A
    concurrent first run may win the create race; its regular file is retained
    without treating that as a failure.  Persistence is advisory once the
    operation's roots have been resolved.
    """
    if source != "defaults":
        return
    destination = _user_config_path()
    try:
        if existing_regular_file(destination, "configuration file", required=False) is not None:
            return
        snapshot = resolve_application_config(None, source="defaults").config
        if snapshot.storage.data_root is None or snapshot.plugins.root is None:
            raise ConfigurationError("effective configuration has no absolute storage or plugin root")
        _write_initial_user_config(destination, snapshot)
    except (ConfigurationError, OSError) as exc:
        # Opening with ``x`` is intentionally race-safe.  If another process
        # won after the check above, preserve its configuration.
        try:
            if existing_regular_file(destination, "configuration file", required=False) is not None:
                return
        except ConfigurationError:
            pass
        print(f"warning: could not create initial user configuration: {exc}", file=sys.stderr)
    else:
        print(f"Created user configuration: {destination}", file=sys.stderr)


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
    if getattr(args, "existing_file", None):
        patch["output"] = {"existing_file": args.existing_file}
    if getattr(args, "image_format", None):
        existing_output = patch.get("output")
        output = dict(existing_output) if isinstance(existing_output, Mapping) else {}
        output["image_format"] = args.image_format
        patch["output"] = output
    if getattr(args, "no_console_log", False) or args.json_output:
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
    rewrite_user_layers: bool = False,
) -> tuple[AppConfig, Path, Path, str]:
    resolved = _resolved_config_for(args, site, rewrite_user_layers=rewrite_user_layers)
    return (
        resolved.config,
        resolved.config_root,
        resolved.main_config_path or _bundled_config_path(),
        resolved.source,
    )


def _resolved_config_for(
    args: argparse.Namespace,
    site: str | None,
    *,
    rewrite_user_layers: bool = False,
) -> ResolvedApplicationConfig:
    """Choose the single user config location, then resolve without prompting or writing files."""
    if args.config is not None:
        if not args.config.is_absolute():
            raise ConfigurationError("--config must be an absolute path")
        raw_config_path, source = args.config, "explicit"
    else:
        user_config = _user_config_path()
        if existing_regular_file(user_config, "configuration file", required=False) is not None:
            raw_config_path, source = user_config, "user"
        else:
            raw_config_path, source = None, "defaults"
    if (
        raw_config_path is not None
        and existing_regular_file(raw_config_path, "configuration file", required=False) is None
    ):
        config_path = canonical_path(raw_config_path, "configuration path")
        raise ConfigurationError(
            f"configuration file not found: {config_path}; create it with 'config init \"{config_path}\"'"
        )
    patch = dict(_bootstrap_override(args))
    patch.update(_app_override(args))
    resolved = resolve_application_config(
        raw_config_path,
        args.profile,
        site,
        require_config=raw_config_path is not None,
        runtime_override=patch or None,
        source=source,
        rewrite_user_layers=rewrite_user_layers,
    )
    paths = resolve_paths(resolved.config)
    effective_plugin_root = _plugin_root(args, resolved.config)
    if source == "user":
        assert resolved.main_config_path is not None
        print(f"Using user configuration: {resolved.main_config_path}", file=sys.stderr)
        print(f"Downloads: {paths['downloads']}", file=sys.stderr)
        print(f"Plugin root: {effective_plugin_root}", file=sys.stderr)
        print(f"Plugin verification: {resolved.config.security.plugin_verification}", file=sys.stderr)
    return resolved
