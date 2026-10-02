"""Shared CLI command protocol and option validation."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from typing import Protocol

from ..exceptions import ConfigurationError


class CommandHandler(Protocol):
    async def handle(self, args: argparse.Namespace) -> int: ...


_OPTION_DEFAULTS: Mapping[str, object] = {
    "selection_priority": 0,
    "plugin_config": [],
    "plugin_config_file": [],
    "plugin_id": None,
    "force_plugin_id": None,
    "plugin_download_policy": [],
    "plugin_download_policy_file": [],
    "fallback_generic": "auto",
    "host": None,
    "no_console_log": False,
    "list_updated_urls": False,
    "inspect_only": False,
    "manifest_only": False,
    "export_cookies": None,
    "import_cookies": None,
    "import_browser_cookies": None,
    "existing_file": None,
    "image_format": None,
    "force_image_format": None,
    "output_dir": None,
    "directory_format": None,
    "inspection_data": None,
    "plugin_root": None,
    "plugin_verification_override": None,
    "yes": False,
}


def _reject_command_options(args: argparse.Namespace, names: Sequence[str], command: str) -> None:
    for name in names:
        default = _OPTION_DEFAULTS[name]
        if getattr(args, name, default) != default:
            option = name.replace("_", "-")
            raise ConfigurationError(f"--{option} is not valid for the {command} command")
