"""CLI for update discovery, target selection, and durable download workflows."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from contextlib import nullcontext, redirect_stdout
from dataclasses import asdict, replace
from functools import partial
from pathlib import Path
from typing import Literal, cast
from urllib.parse import urlparse

from ..application.additional_files import additional_file_payload
from ..application.composer import RuntimeComposer
from ..application.workflow_recording import _settle, revise_result
from ..application.workflow_reporting import stopped_status, workflow_outcome, workflow_status
from ..application.workflow_selection import FIRST_RUN_NOTE, workflow_selection
from ..configuration.models import AppConfig
from ..configuration.paths import resolve_paths
from ..exceptions import ConfigurationError, error_info_for, error_reason_for_code
from ..models import (
    DownloadResult,
    ImageFailure,
    WorkflowAttemptResult,
    WorkflowPlanResult,
    WorkflowResult,
    WorkflowRoundResult,
)
from ..privacy.log_safety import safe_exception_name, safe_locator, safe_relative_path, safe_url
from ..storage.workflow_logging import WorkflowLogWriter
from .constants import (
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


def _failure_payload(failure: ImageFailure, root: Path) -> dict[str, object]:
    return _safe_error(
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


def _download_payload(result: DownloadResult, root: Path) -> dict[str, object]:
    return {
        "additional_files": additional_file_payload(result, root),
        "saved": [safe_relative_path(path, root) for path in result.saved_files],
        "skipped": [safe_relative_path(path, root) for path in result.skipped_files],
        "failures": [_failure_payload(failure, root) for failure in result.failures],
    }


def _attempt_payload(attempt: WorkflowAttemptResult, root: Path) -> dict[str, object]:
    return {
        "round_number": attempt.round_number,
        "status": attempt.status,
        "download": _download_payload(attempt.download, root) if attempt.download is not None else None,
        "error": _safe_error(asdict(attempt.error), root) if attempt.error is not None else None,
        "images": [
            {
                "chapter_position": image.chapter_position,
                "image_position": image.image_position,
                "locator": safe_locator(image.image.url),
                "status": image.status,
                "retained": image.retained,
                "attempted": image.attempted,
                "failure": _failure_payload(image.outcome.failure, root)
                if image.outcome is not None and image.outcome.failure is not None
                else None,
                "path": safe_relative_path(image.outcome.path, root)
                if image.outcome is not None and image.outcome.path is not None
                else None,
            }
            for image in attempt.images
        ],
    }


def _round_payload(round_result: WorkflowRoundResult, root: Path) -> dict[str, object]:
    snapshot = round_result.snapshot
    return {
        "round_number": round_result.round_number,
        "status": round_result.status,
        "checked_at": snapshot.checked_at.isoformat() if snapshot is not None else None,
        "candidates": [dict(asdict(c), url=safe_url(c.url)) for c in snapshot.candidates] if snapshot else [],
        "changes": [dict(asdict(c), url=safe_url(c.url)) for c in round_result.changes],
        "selected_urls": [safe_url(url) for url in round_result.selected_urls],
        "removed_urls": [safe_url(url) for url in round_result.removed_urls],
        "error": _safe_error(asdict(round_result.error), root) if round_result.error is not None else None,
    }


def workflow_payload(result: WorkflowResult, root: Path, *, log_root: Path | None = None) -> dict[str, object]:
    log_root = root if log_root is None else log_root
    items = [
        {
            "url": safe_url(item.url),
            "reasons": item.reasons,
            "status": item.status,
            "download": _download_payload(item.download, root) if item.download is not None else None,
            "error": _safe_error(asdict(item.error), root) if item.error is not None else None,
            "attempts": [_attempt_payload(attempt, root) for attempt in item.attempts],
        }
        for item in result.items
    ]
    return {
        "operation": "workflow",
        "outcome": workflow_outcome(result),
        "run_id": result.run_id,
        "history_saved": result.history_saved,
        "history_warning": result.history_warning,
        "workflow_log": {
            **asdict(result.workflow_log),
            "plan_files": [safe_relative_path(path, log_root) for path in result.workflow_log.plan_files],
            "jsonl_path": safe_relative_path(result.workflow_log.jsonl_path, log_root)
            if result.workflow_log.jsonl_path
            else None,
            "text_path": safe_relative_path(result.workflow_log.text_path, log_root)
            if result.workflow_log.text_path
            else None,
        }
        if result.workflow_log is not None
        else None,
        "source_url": safe_url(result.source_url),
        "download_scope": result.download_scope,
        "workflow_retries": result.workflow_retries,
        "workflow_retry_delay": result.workflow_retry_delay,
        "workflow_retry_timeout": result.workflow_retry_timeout,
        "rounds": [_round_payload(round_result, root) for round_result in result.rounds],
        "timed_out": result.timed_out,
        "checked_at": result.snapshot.checked_at.isoformat() if result.snapshot is not None else None,
        "candidates": [dict(asdict(candidate), url=safe_url(candidate.url)) for candidate in result.snapshot.candidates]
        if result.snapshot is not None
        else [],
        "changes": [dict(asdict(change), url=safe_url(change.url)) for change in result.changes],
        "selected_urls": [safe_url(url) for url in result.selected_urls],
        "items": items,
        "summary": {
            status: sum(item.status == status for item in result.items)
            for status in ("success", "partial", "failed", "unprocessed", "removed")
        },
        "stop_error": _safe_error(asdict(result.stop_error), root) if result.stop_error is not None else None,
        "cancelled": result.cancelled,
    }


def _status(result: WorkflowResult) -> int:
    return workflow_status(result)


def _stopped_status(error: BaseException) -> int:
    return stopped_status(error)


class WorkflowCommandHandler:
    async def handle(self, args: argparse.Namespace) -> int:
        try:
            return await self._handle(args)
        except (Exception, asyncio.CancelledError) as exc:
            if not getattr(args, "dry_run", False):
                raise
            result = getattr(
                exc, "workflow_plan_result", WorkflowPlanResult(args.url, args.download_scope or "updated")
            )
            result = replace(
                result,
                stop_error=None if isinstance(exc, asyncio.CancelledError) else error_info_for(exc),
                cancelled=isinstance(exc, asyncio.CancelledError),
            )
            _print_plan(result, args, Path.cwd())
            return _stopped_status(exc)

    async def _handle(self, args: argparse.Namespace) -> int:
        retries = args.workflow_retries if args.workflow_retries is not None else 1
        delay = args.workflow_retry_delay if args.workflow_retry_delay is not None else 600.0
        timeout = args.workflow_retry_timeout
        if retries < 0 or not math.isfinite(delay) or delay < 0:
            raise ConfigurationError("workflow retries and delay must be non-negative and finite")
        if timeout is not None and (not math.isfinite(timeout) or timeout <= 0):
            raise ConfigurationError("workflow retry timeout must be positive and finite")
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
        if getattr(args, "dry_run", False):
            return await _plan_command(args, overrides, policies)
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
                        workflow_retries=retries,
                        workflow_retry_delay=delay,
                        workflow_retry_timeout=timeout,
                        workflow_progress_log=None
                        if args.workflow_progress_log is None
                        else args.workflow_progress_log == "enabled",
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
                    status = _status(result) if result.timed_out else _stopped_status(exc)
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
                        result, status = await _revise_after_close(
                            config, result, status, output_root or resolve_paths(config)["downloads"]
                        )
        assert result is not None
        payload = workflow_payload(
            result, output_root or resolve_paths(config)["downloads"], log_root=resolve_paths(config)["profile"]
        )
        if args.json_output:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            _print_workflow(result, payload)
        return status


def workflow_plan_payload(result: WorkflowPlanResult, root: Path) -> dict[str, object]:
    return {
        "operation": "workflow",
        "dry_run": True,
        "source_url": safe_url(result.source_url),
        "download_scope": result.download_scope,
        "plugin_id": result.plugin_id,
        "first_run": result.first_run,
        "checked_at": result.snapshot.checked_at.isoformat() if result.snapshot is not None else None,
        "candidates": [dict(asdict(c), url=safe_url(c.url)) for c in result.snapshot.candidates]
        if result.snapshot
        else [],
        "changes": [dict(asdict(c), url=safe_url(c.url)) for c in result.changes],
        "selected_urls": [safe_url(url) for url in result.selected_urls],
        "items": [
            {
                "candidate": dict(asdict(i.candidate), url=safe_url(i.candidate.url)),
                "selected": i.selected,
                "reasons": i.reasons,
            }
            for i in result.items
        ],
        "summary": {
            "candidates": len(result.snapshot.candidates) if result.snapshot else 0,
            "selected_urls": len(result.selected_urls),
            "selected_candidates": sum(i.selected for i in result.items),
            "excluded_candidates": sum(not i.selected for i in result.items),
            "removed_candidates": sum(c.kind.value == "removed" for c in result.changes),
        },
        "stop_error": _safe_error(asdict(result.stop_error), root) if result.stop_error else None,
        "cancelled": result.cancelled,
    }


def _print_plan(result: WorkflowPlanResult, args: argparse.Namespace, root: Path) -> None:
    payload = workflow_plan_payload(result, root)
    if args.json_output:
        print(json.dumps(payload, ensure_ascii=False))
        return
    for item in result.items:
        label = "selected" if item.selected else "excluded"
        print(f"{label} ({', '.join(item.reasons)}): {safe_url(item.candidate.url)}")
    for change in result.changes:
        if change.kind.value == "removed":
            print(f"removed: {safe_url(change.url)}")
    print(f"workflow dry-run ({result.download_scope}): {payload['summary']}", file=sys.stderr)
    if result.stop_error:
        print(f"error [{result.stop_error.code}]: {result.stop_error.reason}", file=sys.stderr)
    if result.cancelled:
        print("workflow dry-run cancelled", file=sys.stderr)


async def _plan_command(args: argparse.Namespace, overrides, policies) -> int:
    service = None
    result = WorkflowPlanResult(args.url, args.download_scope or "updated")
    root = _output_root(args) or Path.cwd()
    status = EXIT_SUCCESS
    with redirect_stdout(sys.stderr):
        try:
            config, config_root, _, _ = _config_for(args, urlparse(args.url).hostname, rewrite_user_layers=False)
            root = _output_root(args) or resolve_paths(config)["downloads"]
            service = RuntimeComposer(
                config,
                config_root=config_root,
                plugin_root=_plugin_root(args, config),
                output_root=_output_root(args),
                plugin_verification_override=args.plugin_verification_override,
            )._compose_for_workflow_plan()
            result = await service.plan_workflow(
                args.url,
                download_scope=cast(Literal["all", "updated"], args.download_scope or "updated"),
                plugin_overrides=overrides,
                fallback_override=_fallback(args),
                plugin_id=args.plugin_id or args.force_plugin_id,
                force_plugin=args.force_plugin_id is not None,
                plugin_download_policy_overrides=policies,
            )
        except (Exception, asyncio.CancelledError) as exc:
            result = getattr(exc, "workflow_plan_result", result)
            result = replace(
                result,
                stop_error=None if isinstance(exc, asyncio.CancelledError) else error_info_for(exc),
                cancelled=isinstance(exc, asyncio.CancelledError),
            )
            status = _stopped_status(exc)
        finally:
            if service is not None:
                try:
                    await service.close()
                except (Exception, asyncio.CancelledError) as exc:
                    if result.stop_error is None and not result.cancelled:
                        result = replace(
                            result,
                            stop_error=None if isinstance(exc, asyncio.CancelledError) else error_info_for(exc),
                            cancelled=isinstance(exc, asyncio.CancelledError),
                        )
                        status = _stopped_status(exc)
    _print_plan(result, args, root)
    return status


async def _revise_after_close(
    config: AppConfig, result: WorkflowResult, status: int, root: Path
) -> tuple[WorkflowResult, int]:
    try:
        result = await revise_result(config, result, status, root)
    except asyncio.CancelledError as exc:
        result, status = getattr(exc, "workflow_result", replace(result, cancelled=True)), 130
    writer = WorkflowLogWriter.resume(result)
    if writer is not None:
        cancellation = await _settle(partial(writer.finish, result, status, workflow_outcome(result), correction=True))
        if cancellation is not None:
            result, status = replace(result, cancelled=True, stop_error=None), 130
            try:
                result = await revise_result(config, result, status, root)
            except asyncio.CancelledError as exc:
                result = getattr(exc, "workflow_result", result)
            await _settle(partial(writer.finish, result, status, workflow_outcome(result), correction=True))
        result = replace(result, workflow_log=writer.result)
    return result, status


def _print_workflow(result: WorkflowResult, payload: dict[str, object]) -> None:
    for item in result.items:
        print(f"{item.status}: {safe_url(item.url)}")
    print(f"workflow ({result.download_scope}): {workflow_outcome(result)}", file=sys.stderr)
    if result.snapshot is None:
        print("update check result: not established", file=sys.stderr)
    for line in workflow_selection(result):
        print(line, file=sys.stderr)
    print(FIRST_RUN_NOTE, file=sys.stderr)
    summary = cast(dict[str, int], payload["summary"])
    print("URL results: " + " ".join(f"{name}={count}" for name, count in summary.items()), file=sys.stderr)
    if result.stop_error is not None:
        print(f"error [{result.stop_error.code}]: {result.stop_error.reason}", file=sys.stderr)
    if result.cancelled:
        print("workflow cancelled", file=sys.stderr)
