"""cookie CLI command implementation."""

from __future__ import annotations

import asyncio
import getpass
import sys
from pathlib import Path

from ..configuration.paths import resolve_paths
from ..credentials.vault import CredentialVault
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
        credentials = CredentialVault.from_config(config).profile(config.profile.default)
        cookie_store = CookieStore(FileSystem(resolve_paths(config)["cookie"]), store=credentials)
        if action == "export":
            await asyncio.to_thread(cookie_store.export_file, Path(value), getpass.getpass("Passphrase: "))
        elif action == "import":
            await asyncio.to_thread(cookie_store.import_file, Path(value), getpass.getpass("Passphrase: "))
        else:
            if args.chrome_user_data_dir is None:
                await asyncio.to_thread(cookie_store.import_browser, str(value))
            elif sys.platform == "win32":
                # browser-cookie3 imports WMI/COM even for explicit snapshots.
                # Keep those process-lived references on the CLI owner thread;
                # a short-lived executor thread can leave invalid COM references.
                cookie_store.import_browser(str(value), chrome_user_data_dir=args.chrome_user_data_dir)
            else:
                await asyncio.to_thread(
                    cookie_store.import_browser, str(value), chrome_user_data_dir=args.chrome_user_data_dir
                )
        # Cookies, passphrases and browser records never enter presentation data.
        return outcome.set_result({"operation": "cookie", "action": action, "target": str(value)}, EXIT_SUCCESS)
