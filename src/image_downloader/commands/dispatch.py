"""CLI dispatch and process exit boundary."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Mapping

from ..exceptions import AuthenticationError, ConfigurationError, DownloaderError, PluginError
from .config import ConfigCommandHandler
from .constants import EXIT_AUTHENTICATION, EXIT_CONFIGURATION, EXIT_FAILURE, EXIT_PLUGIN
from .cookie import CookieCommandHandler
from .doctor import DoctorCommandHandler
from .download import DownloadCommandHandler
from .parser import build_parser
from .plugin import PluginCommandHandler
from .validation import CommandHandler

_COMMAND_HANDLERS: Mapping[str, CommandHandler] = {
    "download": DownloadCommandHandler(),
    "cookie": CookieCommandHandler(),
    "doctor": DoctorCommandHandler(),
    "config": ConfigCommandHandler(),
    "plugin": PluginCommandHandler(),
}


async def run(args: argparse.Namespace) -> int:
    handler_name = getattr(args, "command_handler", None)
    if handler_name is None and any(
        getattr(args, name, None) is not None for name in ("export_cookies", "import_cookies", "import_browser_cookies")
    ):
        handler_name = "cookie"
    if handler_name is None:
        raise ValueError("URL or command is required")
    return await _COMMAND_HANDLERS[handler_name].handle(args)


def main() -> int:
    try:
        return asyncio.run(run(build_parser().parse_args()))
    except KeyboardInterrupt:
        return 130
    except AuthenticationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_AUTHENTICATION
    except PluginError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_PLUGIN
    except (ConfigurationError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONFIGURATION
    except (DownloaderError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILURE
