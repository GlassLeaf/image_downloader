"""CLI for update discovery, target selection, and durable download workflows."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from contextlib import nullcontext, redirect_stdout
from dataclasses import asdict, replace
from pathlib import Path
from typing import Literal, cast
from urllib.parse import urlparse

from ..application.composer import RuntimeComposer
from ..configuration.paths import resolve_paths
from ..exceptions import AuthenticationError, ConfigurationError, PluginError, error_info_for, error_reason_for_code
from ..models import DownloadResult, WorkflowResult
from ..privacy.log_safety import safe_exception_name, safe_relative_path, safe_url
from .constants import (
    EXIT_AUTHENTICATION,
    EXIT_CONFIGURATION,
    EXIT_FAILURE,
    EXIT_PARTIAL,
    EXIT_PLUGIN,
    EXIT_SUCCESS,
)
from .setup import (
    _config_for,
    _download_policy_overrides,
    _fallback,
    _output_root,
    _persist_initial_user_config,
    _plugin_root,
    _runtime_overrides,
)
from .validation import _reject_command_options


def _safe_error(payload: dict[str, object], root: Path) -> dict[str, object]:
    if isinstance(payload.get("response_url"), str):
        payload["response_url"] = safe_url(str(payload["response_url"]))
    if isinstance(payload.get("output_path"), str) and Path(str(payload["output_path"])).is_absolute():
        payload["output_path"] = safe_relative_path(str(payload["output_path"]), root)
    return payload


def _download_payload(result: DownloadResult, root: Path) -> dict[str, object]:
    return {
        "saved": result.saved_files,
        "skipped": result.skipped_files,
        "failures": [
            _safe_error(
                {
                    "kind": failure.kind,
                    "exception": safe_exception_name(failure.exception_type),
                    "message": error_reason_for_code(failure.code) or "image failure",
                    "code": failure.code,
                    "reason": error_reason_for_code(failure.code) or "image failure",
                    "output_path": failure.output_path,
                    "response_url": failure.response_url,
                    "http_status": failure.http_status,
                    "transport": failure.transport,
                },
                root,
            )
            for failure in result.failures
        ],
    }


def workflow_payload(result: WorkflowResult, root: Path) -> dict[str, object]:
    items = [
        {
            "url": safe_url(item.url),
            "reasons": item.reasons,
            "status": item.status,
            "download": _download_payload(item.download, root) if item.download is not None else None,
            "error": _safe_error(asdict(item.error), root) if item.error is not None else None,
        }
        for item in result.items
    ]
    return {
        "operation": "workflow",
        "source_url": safe_url(result.source_url),
        "download_scope": result.download_scope,
        "checked_at": result.snapshot.checked_at.isoformat() if result.snapshot is not None else None,
        "candidates": [dict(asdict(candidate), url=safe_url(candidate.url)) for candidate in result.snapshot.candidates]
        if result.snapshot is not None
        else [],
        "changes": [dict(asdict(change), url=safe_url(change.url)) for change in result.changes],
        "selected_urls": [safe_url(url) for url in result.selected_urls],
        "items": items,
        "summary": {
            status: sum(item.status == status for item in result.items)
            for status in ("success", "partial", "failed", "unprocessed")
        },
        "stop_error": _safe_error(asdict(result.stop_error), root) if result.stop_error is not None else None,
        "cancelled": result.cancelled,
    }


def _status(result: WorkflowResult) -> int:
    if all(item.status == "success" for item in result.items):
        return EXIT_SUCCESS
    return (
        EXIT_PARTIAL
        if any(
            item.download is not None and (item.download.saved_files or item.download.skipped_files)
            for item in result.items
        )
        else EXIT_FAILURE
    )


def _stopped_status(error: BaseException) -> int:
    if isinstance(error, asyncio.CancelledError):
        return 130
    if isinstance(error, ConfigurationError):
        return EXIT_CONFIGURATION
    if isinstance(error, AuthenticationError):
        return EXIT_AUTHENTICATION
    return EXIT_PLUGIN if isinstance(error, PluginError) else EXIT_FAILURE


class WorkflowCommandHandler:
    async def handle(self, args: argparse.Namespace) -> int:
        if args.image_format is not None and args.force_image_format is not None:
            raise ConfigurationError("--image-format cannot be combined with --force-image-format")
        if args.plugin_id is not None and args.force_plugin_id is not None:
            raise ConfigurationError("--plugin cannot be combined with --force-plugin")
        _reject_command_options(
            args,
            (
                "list_updated_urls",
                "inspect_only",
                "manifest_only",
                "inspection_data",
                "host",
                "selection_priority",
                "export_cookies",
                "import_cookies",
                "import_browser_cookies",
            ),
            "workflow",
        )
        overrides = _runtime_overrides(args)
        policies = _download_policy_overrides(args)
        config, config_root, _, source = _config_for(
            args,
            urlparse(args.url).hostname,
            rewrite_user_layers=True,
        )
        _persist_initial_user_config(source)
        output_root = _output_root(args)
        service = RuntimeComposer(
            config,
            config_root=config_root,
            plugin_root=_plugin_root(args, config),
            output_root=output_root,
            plugin_verification_override=args.plugin_verification_override,
        ).compose()
        diagnostics = redirect_stdout(sys.stderr) if args.json_output else nullcontext()
        result: WorkflowResult | None = None
        try:
            with diagnostics:
                try:
                    result = await service.workflow(
                        args.url,
                        download_scope=cast(Literal["all", "updated"], args.download_scope or "updated"),
                        plugin_overrides=overrides,
                        fallback_override=_fallback(args),
                        plugin_id=args.plugin_id or args.force_plugin_id,
                        force_plugin=args.force_plugin_id is not None,
                        plugin_download_policy_overrides=policies,
                        force_image_format=args.force_image_format,
                    )
                    status = _status(result)
                except (Exception, asyncio.CancelledError) as exc:
                    stopped_result = getattr(exc, "workflow_result", None)
                    if not isinstance(stopped_result, WorkflowResult):
                        raise
                    result = stopped_result
                    status = _stopped_status(exc)
        finally:
            with diagnostics:
                try:
                    await service.close()
                except (Exception, asyncio.CancelledError) as exc:
                    if result is None:
                        raise
                    if result.stop_error is None and not result.cancelled:
                        result = replace(
                            result,
                            stop_error=None if isinstance(exc, asyncio.CancelledError) else error_info_for(exc),
                            cancelled=isinstance(exc, asyncio.CancelledError),
                        )
                        status = _stopped_status(exc)
        assert result is not None
        payload = workflow_payload(result, output_root or resolve_paths(config)["downloads"])
        if args.json_output:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            for item in result.items:
                print(f"{item.status}: {safe_url(item.url)}")
            print(f"workflow ({result.download_scope}): {payload['summary']}", file=sys.stderr)
            if result.stop_error is not None:
                print(f"error [{result.stop_error.code}]: {result.stop_error.reason}", file=sys.stderr)
            if result.cancelled:
                print("workflow cancelled", file=sys.stderr)
        return status
