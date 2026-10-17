"""Render failures and map causes to stable CLI status codes."""

from __future__ import annotations

import json
import sys

from ..diagnostics import diagnostic_for
from ..exceptions import (
    AuthenticationError,
    ConfigurationError,
    ErrorInfo,
    ImageDownloaderError,
    ImageFailureContext,
    PluginError,
    error_info_for,
    operation_cause,
)
from ..observability.logging import safe_locator
from ..privacy.log_safety import safe_url
from .constants import EXIT_AUTHENTICATION, EXIT_CONFIGURATION, EXIT_FAILURE, EXIT_PLUGIN


def _cli_error_info(error: Exception) -> ErrorInfo:
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
    cause = operation_cause(error)
    diagnostic = diagnostic_for(cause)
    if diagnostic is not None:
        payload["message"] = diagnostic.message
        payload["details"] = dict(diagnostic.details)
    if info.response_url is not None:
        payload["response_url"] = safe_url(info.response_url)
    if info.http_status is not None:
        payload["http_status"] = info.http_status
    if info.output_path is not None:
        payload["output_path"] = info.output_path
    context = cause.image_failure if isinstance(cause, ImageDownloaderError) else None
    if isinstance(context, ImageFailureContext):
        for field, value in (("stage", context.stage), ("transport", context.transport)):
            if isinstance(value, str):
                payload[field] = value
        if isinstance(context.image_url, str):
            payload["image_url"] = safe_locator(context.image_url)
        if isinstance(context.response_url, str):
            payload["response_url"] = safe_url(context.response_url)
        if isinstance(context.http_status, int):
            payload["http_status"] = context.http_status
        if isinstance(context.output_path, str):
            payload["output_path"] = context.output_path
    return payload


def _exit_status(error: BaseException) -> int:
    error = operation_cause(error)
    if isinstance(error, AuthenticationError):
        return EXIT_AUTHENTICATION
    if isinstance(error, PluginError):
        return EXIT_PLUGIN
    if isinstance(error, ConfigurationError):
        return EXIT_CONFIGURATION
    return EXIT_FAILURE


def _render_error(error: Exception, *, json_output: bool, operation: str) -> int:
    payload = _error_payload(error, operation=operation)
    if json_output:
        print(json.dumps({"error": payload}, ensure_ascii=False))
    else:
        print(f"error [{payload['code']}]: {payload['message']}", file=sys.stderr)
    return _exit_status(error)
