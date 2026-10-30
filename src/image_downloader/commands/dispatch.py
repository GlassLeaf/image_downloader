"""CLI dispatch and process exit boundary."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Mapping, Sequence
from typing import cast

from .config import ConfigCommandHandler
from .cookie import CookieCommandHandler
from .credentials import CredentialCommandHandler, VaultCommandHandler
from .doctor import DoctorCommandHandler
from .download import DownloadCommandHandler
from .inspect import InspectCommandHandler
from .outcome import CommandOutcome, json_mode
from .parser import _CliArgumentParser, build_parser
from .plugin import PluginCommandHandler
from .prepared import CommandHandler
from .state import StateCommandHandler
from .validation import validate_arguments
from .verify import VerifyCommandHandler
from .workflow import WorkflowCommandHandler

_COMMAND_HANDLERS: Mapping[str, CommandHandler] = {
    "credential": CredentialCommandHandler(),
    "vault": VaultCommandHandler(),
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
    outcome = await _execute(args)
    return outcome.finish(propagate=True)


async def _execute(args: argparse.Namespace) -> CommandOutcome:
    outcome = CommandOutcome(
        _operation_name((), args),
        json_output=json_mode(args),
        pretty=not bool(getattr(args, "json_output", False)),
    )
    with outcome.diagnostics():
        try:
            command = validate_arguments(args)
            outcome.no_shutdown()
            status = await _COMMAND_HANDLERS[command.name].handle(command, outcome)
            if outcome.execution.result is None and outcome.execution.failure is None:
                outcome.set_result({}, status)
        except BaseException as error:
            outcome.fail(error)
    return outcome


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
        json_output = json_mode(parsed)
        outcome = asyncio.run(_execute(parsed))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        outcome = CommandOutcome(
            _operation_name(source, parsed) if parsed is not None else getattr(parser, "operation", "cli"),
            json_output=json_output or getattr(parser, "json_error_mode", False),
        )
        outcome.fail(exc)
    try:
        return outcome.finish()
    except KeyboardInterrupt:
        return 130
