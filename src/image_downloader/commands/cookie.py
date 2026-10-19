"""cookie CLI command implementation."""

from __future__ import annotations

import asyncio
import getpass
from pathlib import Path

from ..configuration.paths import resolve_paths
from ..storage import FileSystem
from ..storage.cookies import CookieStore
from .constants import EXIT_SUCCESS
from .outcome import CommandOutcome
from .prepared import CookieArguments, PreparedCommand
from .setup import (
    _config_for,
    _persist_initial_user_config,
)


class CookieCommandHandler:
    async def handle(self, command: PreparedCommand, outcome: CommandOutcome) -> int:
        args = command.arguments
        assert isinstance(args, CookieArguments)
        action, value = args.cookie_action, args.cookie_value
        config, _, _, source = _config_for(args, None, rewrite_user_layers=True)
        _persist_initial_user_config(source)
        cookie_store = CookieStore(FileSystem(resolve_paths(config)["cookie"]))
        if action == "export":
            await asyncio.to_thread(cookie_store.export_file, Path(value), getpass.getpass("Passphrase: "))
        elif action == "import":
            await asyncio.to_thread(cookie_store.import_file, Path(value), getpass.getpass("Passphrase: "))
        else:
            await asyncio.to_thread(cookie_store.import_browser, str(value))
        # Cookies, passphrases and browser records never enter presentation data.
        return outcome.set_result({"operation": "cookie", "action": action, "target": str(value)}, EXIT_SUCCESS)
