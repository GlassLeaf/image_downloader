"""CLI dispatch and process exit boundary."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Mapping, Sequence

from ..exceptions import (
    AuthenticationError,
    ConfigurationError,
    ErrorInfo,
    ImageDownloaderError,
    PluginError,
    error_info_for,
)
from ..privacy.log_safety import safe_url
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


def _requested_json(argv: Sequence[str]) -> bool:
    return "--json" in argv


def _operation_name(argv: Sequence[str], parsed: argparse.Namespace | None = None) -> str:
    handler = getattr(parsed, "command_handler", None) if parsed is not None else None
    if handler in _COMMAND_HANDLERS:
        return str(handler)
    commands = {"download", "cookie", "doctor", "config", "plugin"}
    return next((value for value in argv if value in commands), "download")


def _cli_error_info(error: Exception) -> ErrorInfo:
    if isinstance(error, ValueError) and not isinstance(error, ImageDownloaderError):
        return error_info_for(ConfigurationError())
    return error_info_for(error)


def _error_payload(error: Exception, *, operation: str) -> dict[str, object]:
    info = _cli_error_info(error)
    payload: dict[str, object] = {
        "code": info.code,
        "reason": info.reason,
        "exception": info.exception,
        "message": info.message,
        "operation": operation,
    }
    if info.response_url is not None:
        payload["response_url"] = safe_url(info.response_url)
    if info.http_status is not None:
        payload["http_status"] = info.http_status
    if info.output_path is not None:
        payload["output_path"] = info.output_path
    context = getattr(error, "_image_failure_context", None)
    if isinstance(context, Mapping):
        for field in ("stage", "transport"):
            if isinstance(context.get(field), str):
                payload[field] = context[field]
        for field in ("image_url", "response_url"):
            if isinstance(context.get(field), str):
                payload[field] = safe_url(context[field])
        if isinstance(context.get("http_status"), int):
            payload["http_status"] = context["http_status"]
        if isinstance(context.get("output_path"), str):
            payload["output_path"] = context["output_path"]
    return payload


def _exit_status(error: Exception) -> int:
    if isinstance(error, AuthenticationError):
        return EXIT_AUTHENTICATION
    if isinstance(error, PluginError):
        return EXIT_PLUGIN
    if isinstance(error, (ConfigurationError, ValueError)):
        return EXIT_CONFIGURATION
    return EXIT_FAILURE


def _render_error(error: Exception, *, json_output: bool, operation: str) -> int:
    payload = _error_payload(error, operation=operation)
    if json_output:
        print(json.dumps({"error": payload}, ensure_ascii=False))
    else:
        print(f"error [{payload['code']}]: {payload['message']}", file=sys.stderr)
    return _exit_status(error)


def main(argv: Sequence[str] | None = None) -> int:
    source = tuple(sys.argv[1:] if argv is None else argv)
    parsed: argparse.Namespace | None = None
    json_output = _requested_json(source)
    try:
        parsed = build_parser().parse_args(source)
        json_output = bool(getattr(parsed, "json_output", False))
        return asyncio.run(run(parsed))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        return _render_error(exc, json_output=json_output, operation=_operation_name(source, parsed))
