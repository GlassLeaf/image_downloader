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
from ..observability.logging import safe_locator
from ..privacy.log_safety import safe_url
from .config import ConfigCommandHandler
from .constants import EXIT_AUTHENTICATION, EXIT_CONFIGURATION, EXIT_FAILURE, EXIT_PLUGIN
from .cookie import CookieCommandHandler
from .doctor import DoctorCommandHandler
from .download import DownloadCommandHandler
from .inspect import InspectCommandHandler
from .parser import build_parser
from .plugin import PluginCommandHandler
from .validation import CommandHandler
from .workflow import WorkflowCommandHandler

_COMMAND_HANDLERS: Mapping[str, CommandHandler] = {
    "download": DownloadCommandHandler(),
    "workflow": WorkflowCommandHandler(),
    "inspect": InspectCommandHandler(),
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
    if handler_name != "workflow" and getattr(args, "download_scope", None) is not None:
        raise ConfigurationError("--download-scope is only valid for workflow")
    for option in ("workflow_retries", "workflow_retry_delay", "workflow_retry_timeout"):
        if handler_name != "workflow" and getattr(args, option, None) is not None:
            raise ConfigurationError(f"--{option.replace('_', '-')} is only valid for workflow")
    return await _COMMAND_HANDLERS[handler_name].handle(args)


def _requested_json(argv: Sequence[str]) -> bool:
    return "--json" in argv


def _operation_name(argv: Sequence[str], parsed: argparse.Namespace | None = None) -> str:
    handler = getattr(parsed, "command_handler", None) if parsed is not None else None
    if handler in _COMMAND_HANDLERS:
        return str(handler)
    commands = {"download", "workflow", "inspect", "cookie", "doctor", "config", "plugin"}
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
        if isinstance(context.get("image_url"), str):
            payload["image_url"] = safe_locator(context["image_url"])
        if isinstance(context.get("response_url"), str):
            payload["response_url"] = safe_url(context["response_url"])
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
