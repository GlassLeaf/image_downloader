"""Configuration discovery, initialization, and effective-value explanation."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlparse

from ..configuration.hosts import normalize_host
from ..configuration.paths import default_data_root, default_plugin_root, default_user_config_path
from ..exceptions import ConfigurationError
from ..storage.path_safety import canonical_path, existing_directory, existing_regular_file
from . import setup as cli_setup
from .constants import EXIT_SUCCESS
from .reporting import _doctor_redact
from .setup import (
    _app_override,
    _bootstrap_override,
    _resolved_config_for,
    _write_user_config,
)
from .validation import _reject_command_options

_EXPLAIN_ALLOWED = frozenset(("host", "no_console_log", "existing_file", "image_format"))


class ConfigCommandHandler:
    async def handle(self, args: argparse.Namespace) -> int:
        return config_command(args)


def _reject_except(args: argparse.Namespace, allowed: frozenset[str]) -> None:
    candidates = (
        "selection_priority",
        "plugin_config",
        "plugin_config_file",
        "fallback_generic",
        "host",
        "no_console_log",
        "list_updated_urls",
        "export_cookies",
        "import_cookies",
        "import_browser_cookies",
        "existing_file",
        "image_format",
    )
    _reject_command_options(args, tuple(name for name in candidates if name not in allowed), "config")


def _reject_configuration_options(args: argparse.Namespace, allowed: frozenset[str]) -> None:
    supplied = {
        "config": args.config is not None,
        "profile": args.profile is not None,
        "data_root": args.data_root is not None,
        "plugin_root": args.plugin_root is not None,
        "yes": args.yes,
    }
    for name, is_supplied in supplied.items():
        if is_supplied and name not in allowed:
            raise ConfigurationError(f"--{name.replace('_', '-')} is not valid for this config command")


def _path_payload() -> dict[str, object]:
    user_config = default_user_config_path()
    return {
        "user_config": str(user_config),
        "user_config_exists": existing_regular_file(user_config, "configuration file", required=False) is not None,
        "package_baseline": str(Path(__file__).parents[1] / "app.yaml"),
        "default_data_root": str(default_data_root()),
        "default_plugin_root": str(default_plugin_root()),
    }


def _config_path(args: argparse.Namespace) -> int:
    _reject_except(args, frozenset())
    _reject_configuration_options(args, frozenset())
    payload = _path_payload()
    if args.json_output:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"user configuration: {payload['user_config']}")
        print(f"exists: {payload['user_config_exists']}")
        print(f"package baseline: {payload['package_baseline']}")
        print(f"automatic data root: {payload['default_data_root']}")
        print(f"automatic plugin root: {payload['default_plugin_root']}")
    return EXIT_SUCCESS


def _explain_site(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = urlparse(value)
    if parsed.scheme:
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ConfigurationError("config explain --host must be a bare host or absolute HTTP(S) URL")
        return parsed.hostname
    return normalize_host(value)[0]


def _config_explain(args: argparse.Namespace) -> int:
    _reject_except(args, _EXPLAIN_ALLOWED)
    _reject_configuration_options(
        args,
        frozenset(("config", "profile", "data_root", "plugin_root")),
    )
    site = _explain_site(args.host)
    resolved = _resolved_config_for(args, site)
    runtime_overrides: dict[str, object] = {
        **_bootstrap_override(args),
        **_app_override(args),
        "plugin_verification_override": args.plugin_verification_override,
    }
    payload = {
        "source": resolved.source,
        "main_config_kind": resolved.main_config_kind,
        "main_config": str(resolved.main_config_path) if resolved.main_config_path is not None else None,
        "config_root": str(resolved.config_root),
        "selected_profile": resolved.selected_profile,
        "target_host": site,
        "layers": [
            {"role": layer.role, "path": str(layer.path) if layer.path is not None else None, "status": layer.status}
            for layer in resolved.layers
        ],
        "effective": _doctor_redact(resolved.config.model_dump(by_alias=True, warnings=False)),
        "origins": dict(resolved.origins),
        "runtime_overrides": _doctor_redact(runtime_overrides),
    }
    if args.json_output:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"source: {payload['source']}")
        print(f"main configuration kind: {payload['main_config_kind']}")
        print(f"main configuration: {payload['main_config'] or '(none; package defaults only)'}")
        print(f"profile: {payload['selected_profile']}")
        print("layers:")
        for layer in payload["layers"]:
            print(f"  {layer['status']:14} {layer['role']:16} {layer['path'] or '-'}")
        print("effective configuration:")
        print(json.dumps(payload["effective"], ensure_ascii=False, indent=2))
        print("value origins:")
        for key, origin in payload["origins"].items():
            print(f"  {key}: {origin}")
        print("CLI runtime overrides:")
        print(json.dumps(payload["runtime_overrides"], ensure_ascii=False, indent=2))
    return EXIT_SUCCESS


def _init_destination(words: list[str]) -> Path:
    if len(words) == 1:
        return cli_setup._user_config_path()
    if len(words) == 2:
        candidate = Path(words[1])
        if not candidate.is_absolute():
            raise ConfigurationError("config init path must be absolute")
        return canonical_path(candidate, "configuration path")
    raise ConfigurationError("config init is 'config init [ABSOLUTE_PATH]'")


def _config_init(args: argparse.Namespace, words: list[str]) -> int:
    _reject_except(args, frozenset())
    _reject_configuration_options(args, frozenset(("data_root", "plugin_root", "yes")))
    destination = _init_destination(words)
    if existing_regular_file(destination, "configuration file", required=False) is not None:
        raise ConfigurationError(f"configuration file already exists: {destination}")
    bootstrap = _bootstrap_override(args)
    storage = bootstrap.get("storage", {})
    plugins = bootstrap.get("plugins", {})
    data_root = Path(storage["data_root"]) if isinstance(storage, dict) and "data_root" in storage else None
    plugin_root = Path(plugins["root"]) if isinstance(plugins, dict) and "root" in plugins else None
    _write_user_config(destination, data_root, plugin_root)
    if args.json_output:
        print(json.dumps({"created_config": str(destination)}, ensure_ascii=False))
    else:
        print(f"created configuration: {destination}")
    return EXIT_SUCCESS


def _profile_init(args: argparse.Namespace, words: list[str]) -> int:
    _reject_except(args, frozenset())
    _reject_configuration_options(args, frozenset(("config", "yes")))
    if len(words) != 3 or words[:2] != ["profile", "init"]:
        raise ConfigurationError("config profile init is 'config profile init NAME'")
    name = words[2]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise ConfigurationError("profile name must contain only letters, numbers, '_' or '-'")
    if args.config is not None:
        if not args.config.is_absolute():
            raise ConfigurationError("--config must be an absolute path")
        main = canonical_path(args.config, "configuration path")
    else:
        main = cli_setup._user_config_path()
    created_main_config = existing_regular_file(main, "configuration file", required=False) is None
    if created_main_config:
        _write_user_config(main, None, None)
    config_root = existing_directory(main.parent, "configuration root", required=True)
    destination = canonical_path(config_root / "profiles" / name / "app.yaml", "profile configuration path")
    if existing_regular_file(destination, "profile configuration file", required=False) is not None:
        raise ConfigurationError(f"profile configuration already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    existing_directory(destination.parent, "profile configuration directory", required=True)
    try:
        with destination.open("x", encoding="utf-8") as stream:
            stream.write("# Profile-specific overrides. profile/storage/plugins are not allowed here.\n{}\n")
    except FileExistsError as exc:
        raise ConfigurationError(f"profile configuration already exists: {destination}") from exc
    if args.json_output:
        print(
            json.dumps(
                {
                    "main_config": str(main),
                    "created_main_config": created_main_config,
                    "profile_config": str(destination),
                },
                ensure_ascii=False,
            )
        )
    else:
        if created_main_config:
            print(f"created configuration: {main}")
        print(f"created profile configuration: {destination}")
    return EXIT_SUCCESS


def config_command(args: argparse.Namespace) -> int:
    words = list(args.command_args)
    if words == ["path"]:
        return _config_path(args)
    if words == ["explain"]:
        return _config_explain(args)
    if words and words[0] == "init":
        return _config_init(args, words)
    if words[:2] == ["profile", "init"]:
        return _profile_init(args, words)
    raise ConfigurationError(
        "config command is 'config path', 'config explain [--host HOST]', 'config init [ABSOLUTE_PATH]', "
        "or 'config profile init NAME'"
    )
