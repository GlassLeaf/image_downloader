"""cookie CLI command implementation."""

from __future__ import annotations

import argparse
import asyncio
import getpass
from pathlib import Path

from ..configuration.paths import resolve_paths
from ..storage import FileSystem
from ..storage.cookies import CookieStore
from .constants import EXIT_SUCCESS
from .setup import (
    _config_for,
)
from .validation import _reject_command_options


class CookieCommandHandler:
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
                "existing_file",
                "image_format",
            ),
            "cookie",
        )
        action, value = self._operation(args)
        config, _, _, _ = _config_for(args, None, allow_root_setup=True)
        cookie_store = CookieStore(FileSystem(resolve_paths(config)["cookie"]))
        if action == "export":
            await asyncio.to_thread(cookie_store.export_file, Path(value), getpass.getpass("Passphrase: "))
        elif action == "import":
            await asyncio.to_thread(cookie_store.import_file, Path(value), getpass.getpass("Passphrase: "))
        else:
            await asyncio.to_thread(cookie_store.import_browser, str(value))
        return EXIT_SUCCESS

    @staticmethod
    def _operation(args: argparse.Namespace) -> tuple[str, str | Path]:
        explicit_action = getattr(args, "cookie_action", None)
        if explicit_action is not None:
            return explicit_action, args.cookie_value
        legacy = [
            ("export", getattr(args, "export_cookies", None)),
            ("import", getattr(args, "import_cookies", None)),
            ("browser-import", getattr(args, "import_browser_cookies", None)),
        ]
        selected = [(action, value) for action, value in legacy if value is not None]
        if len(selected) > 1:
            raise ValueError("select only one cookie import or export operation")
        if not selected:
            raise ValueError("cookie operation is required")
        return selected[0]
