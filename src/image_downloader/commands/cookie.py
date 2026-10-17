"""cookie CLI command implementation."""

from __future__ import annotations

import asyncio
import getpass
import json
from pathlib import Path

from ..configuration.paths import resolve_paths
from ..storage import FileSystem
from ..storage.cookies import CookieStore
from .constants import EXIT_SUCCESS
from .prepared import CookieArguments, PreparedCommand
from .setup import (
    _config_for,
    _persist_initial_user_config,
)


class CookieCommandHandler:
    async def handle(self, command: PreparedCommand) -> int:
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
        if args.json_output:
            # Report only the requested target. Cookies, passphrases, and browser
            # records are intentionally never part of machine-readable output.
            print(json.dumps({"operation": "cookie", "action": action, "target": str(value)}, ensure_ascii=False))
        return EXIT_SUCCESS
