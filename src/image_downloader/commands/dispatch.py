"""CLI dispatch and process exit boundary."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Mapping, Sequence
from typing import cast

from .config import ConfigCommandHandler
from .cookie import CookieCommandHandler
from .doctor import DoctorCommandHandler
from .download import DownloadCommandHandler
from .errors import _render_error
from .inspect import InspectCommandHandler
from .parser import _CliArgumentParser, build_parser
from .plugin import PluginCommandHandler
from .prepared import CommandHandler
from .state import StateCommandHandler
from .validation import validate_arguments
from .verify import VerifyCommandHandler
from .workflow import WorkflowCommandHandler

_COMMAND_HANDLERS: Mapping[str, CommandHandler] = {
    "download": DownloadCommandHandler(),
    "workflow": WorkflowCommandHandler(),
    "state": StateCommandHandler(),
    "verify": VerifyCommandHandler(),
    "inspect": InspectCommandHandler(),
    "cookie": CookieCommandHandler(),
    "doctor": DoctorCommandHandler(),
    "config": ConfigCommandHandler(),
    "plugin": PluginCommandHandler(),
}


async def run(args: argparse.Namespace) -> int:
    command = validate_arguments(args)
    return await _COMMAND_HANDLERS[command.name].handle(command)


def _operation_name(argv: Sequence[str], parsed: argparse.Namespace | None = None) -> str:
    handler = getattr(parsed, "command_handler", None) if parsed is not None else None
    if handler in _COMMAND_HANDLERS:
        return str(handler)
    return "cli"


def main(argv: Sequence[str] | None = None) -> int:
    source = tuple(sys.argv[1:] if argv is None else argv)
    parsed: argparse.Namespace | None = None
    json_output = False
    parser = cast(_CliArgumentParser, build_parser())
    parser.structured_errors = True
    try:
        parsed = parser.parse_args(source)
        json_output = bool(getattr(parsed, "json_output", False))
        return asyncio.run(run(parsed))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        return _render_error(
            exc,
            json_output=json_output or getattr(parser, "json_error_mode", False),
            operation=_operation_name(source, parsed) if parsed is not None else getattr(parser, "operation", "cli"),
        )
