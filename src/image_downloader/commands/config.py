"""config CLI command implementation."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from ..configuration.layers import apply_overrides
from ..configuration.models import AppConfig
from ..exceptions import ConfigurationError
from ..storage.path_safety import canonical_path, existing_directory, existing_regular_file
from .constants import EXIT_SUCCESS
from .setup import (
    _bootstrap_override,
    _collect_root_settings,
    _config_for,
    _user_config_path,
    _user_config_yaml,
    _write_user_config,
)
from .validation import _reject_command_options


class ConfigCommandHandler:
    async def handle(self, args: argparse.Namespace) -> int:
        _reject_command_options(
            args,
            (
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
            ),
            "config",
        )
        return config_command(args)


def config_command(args: argparse.Namespace) -> int:
    words = list(args.command_args)
    if len(words) == 3 and words[:2] == ["profile", "init"]:
        name = words[2]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise ConfigurationError("profile name must contain only letters, numbers, '_' or '-'")
        _, config_root, _, source = _config_for(
            args,
            None,
            allow_root_setup=True,
            allow_explicit_root_setup=True,
            require_saved_user_config=True,
        )
        if source == "bundled":
            user_config = _user_config_path()
            raise ConfigurationError(
                "cannot create a profile configuration without a saved user app.yaml; "
                f"run 'config init \"{user_config}\"' first"
            )
        destination = config_root / "profiles" / name / "app.yaml"
        destination = canonical_path(destination, "profile configuration path")
        if existing_regular_file(destination, "profile configuration file", required=False) is not None:
            raise ConfigurationError(f"profile configuration already exists: {destination}")
        existing_directory(config_root, "configuration root", required=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        existing_directory(destination.parent, "profile configuration directory", required=True)
        try:
            with destination.open("x", encoding="utf-8") as stream:
                stream.write("# Profile-specific overrides. profile/storage/plugins are not allowed here.\n{}\n")
        except FileExistsError as exc:
            raise ConfigurationError(f"profile configuration already exists: {destination}") from exc
        print(f"created profile configuration: {destination}")
        return EXIT_SUCCESS
    if len(words) != 2 or words[0] != "init":
        raise ConfigurationError(
            "config command is 'config init ABSOLUTE_PATH [--data-root ABSOLUTE_PATH] [--plugin-root ABSOLUTE_PATH]' "
            "or 'config profile init NAME'"
        )
    destination = Path(words[1])
    if not destination.is_absolute():
        raise ConfigurationError("config init path must be absolute")
    destination = canonical_path(destination, "configuration path")
    if existing_regular_file(destination, "configuration file", required=False) is not None:
        raise ConfigurationError(f"configuration file already exists: {destination}")
    bootstrap = _bootstrap_override(args)
    config = apply_overrides(AppConfig(), bootstrap) if bootstrap else AppConfig()
    data_root, plugins_root, save_roots = _collect_root_settings(
        args,
        config,
        destination,
        allow_noninteractive_unsaved=True,
        yes_confirms_save=True,
    )
    if save_roots:
        _write_user_config(destination, data_root, plugins_root)
        print(f"created configuration: {destination}")
    else:
        print(_user_config_yaml(data_root, plugins_root), end="")
    return EXIT_SUCCESS
