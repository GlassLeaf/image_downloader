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
    "fallback_generic": "auto",
    "host": None,
    "no_console_log": False,
    "list_updated_urls": False,
    "export_cookies": None,
    "import_cookies": None,
    "import_browser_cookies": None,
    "existing_file": None,
    "image_format": None,
}


def _reject_command_options(args: argparse.Namespace, names: Sequence[str], command: str) -> None:
    for name in names:
        default = _OPTION_DEFAULTS[name]
        if getattr(args, name, default) != default:
            option = name.replace("_", "-")
            raise ConfigurationError(f"--{option} is not valid for the {command} command")
