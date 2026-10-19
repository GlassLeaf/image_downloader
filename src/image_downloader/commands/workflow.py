"""CLI for update discovery, target selection, and durable download workflows."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import asdict, replace
from pathlib import Path
from typing import cast
from urllib.parse import urlparse

from ..application.additional_files import additional_file_payload
from ..application.composer import RuntimeComposer
from ..application.requests import WorkflowPlanRequest, WorkflowRequest
from ..application.workflow_finalizer import revise_after_close
from ..application.workflow_reporting import stopped_status, workflow_outcome, workflow_status
from ..application.workflow_selection import FIRST_RUN_NOTE, workflow_selection
from ..configuration.models import AppConfig
from ..configuration.paths import resolve_paths
from ..exceptions import (
    WorkflowCancelledError,
    WorkflowExecutionError,
    WorkflowPlanCancelledError,
    WorkflowPlanError,
    error_info_for,
    error_reason_for_code,
)
from ..models import (
    DownloadResult,
    ImageFailure,
    WorkflowAttemptResult,
    WorkflowPlanResult,
    WorkflowResult,
    WorkflowRoundResult,
)
from ..privacy.log_safety import safe_exception_name, safe_locator, safe_relative_path, safe_url
from ..storage.transactions import settle_operation
from .outcome import CommandOutcome
from .prepared import PreparedCommand, WorkflowArguments
from .setup import (
    _config_for,
    _fallback,
    _output_root,
    _persist_initial_user_config,
    _plugin_root,
)


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
    async def handle(self, command: PreparedCommand, outcome: CommandOutcome) -> int:
        args = command.arguments
        assert isinstance(args, WorkflowArguments)
        outcome.expect_shutdown()
        if args.dry_run:
            return await _plan_command(args, command.plugin_config, command.download_policy, outcome)
        config, config_root, _, source = _config_for(
            args,
            urlparse(args.url).hostname,
            rewrite_user_layers=True,
        )
        _persist_initial_user_config(source)
        output_root = _output_root(args)
        root = output_root or resolve_paths(config)["downloads"]
        log_root = resolve_paths(config)["profile"]
        service = RuntimeComposer(
            config,
            config_root=config_root,
            plugin_root=_plugin_root(args, config),
            output_root=output_root,
            plugin_verification_override=args.plugin_verification_override,
        ).compose()
        result: WorkflowResult | None = None
        async with outcome.closing(service.close, handled=True):
            try:
                result = await service.workflow(
                    WorkflowRequest(
                        url=args.url,
                        download_scope=args.download_scope or "updated",
                        workflow_retries=args.workflow_retries if args.workflow_retries is not None else 1,
                        workflow_retry_delay=args.workflow_retry_delay
                        if args.workflow_retry_delay is not None
                        else 600.0,
                        workflow_retry_timeout=args.workflow_retry_timeout,
                        workflow_progress_log=None
                        if args.workflow_progress_log is None
                        else args.workflow_progress_log == "enabled",
                        plugin_overrides=command.plugin_config,
                        fallback_override=_fallback(args),
                        plugin_id=args.plugin_id or args.force_plugin_id,
                        force_plugin=args.force_plugin_id is not None,
                        plugin_download_policy_overrides=command.download_policy,
                        force_image_format=args.force_image_format,
                    )
                )
            except (WorkflowExecutionError, WorkflowCancelledError) as error:
                result = error.result
                outcome.fail(
                    error, handled=True, status=_status(result) if result.timed_out else _stopped_status(error)
                )
            status = outcome.execution.exit_code if outcome.execution.failure is not None else _status(result)
            payload = workflow_payload(result, root, log_root=log_root)
            outcome.set_result(payload, status or 0, text=lambda: _print_workflow(result, payload))
            outcome.text_handles_error = True
        if result is not None:
            await _finalize_shutdown(config, result, root, log_root, outcome)
        return outcome.exit_code


async def _finalize_shutdown(
    config: AppConfig, result: WorkflowResult, root: Path, log_root: Path, outcome: CommandOutcome
) -> None:
    final_result = result
    error = outcome.shutdown.failure
    if isinstance(error, (Exception, asyncio.CancelledError)) and outcome.execution.failure is None:
        final_result = replace(
            result,
            stop_error=error_info_for(error) if isinstance(error, Exception) else None,
            cancelled=isinstance(error, asyncio.CancelledError),
        )
        # Shield the complete correction so its cancellation cannot replace the
        # already selected close failure or write a conflicting history status.
        settled = await settle_operation(revise_after_close(config, final_result, outcome.exit_code, root))
        if settled.value is not None:
            final_result, _ = settled.value
        if settled.error is not None or settled.cancellation is not None:
            from ..application.shutdown import warn_shutdown_failure

            warn_shutdown_failure()
    final_payload = workflow_payload(final_result, root, log_root=log_root)
    outcome.shutdown = replace(
        outcome.shutdown,
        result={name: final_payload[name] for name in ("history_saved", "history_warning", "workflow_log")},
    )
    outcome.render_text = lambda: _print_workflow(final_result, final_payload)


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


def _print_plan(result: WorkflowPlanResult, root: Path) -> None:
    payload = workflow_plan_payload(result, root)
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


def _capture_plan(result: WorkflowPlanResult, root: Path, outcome: CommandOutcome) -> None:
    failure = outcome.execution.failure
    if isinstance(failure, (Exception, asyncio.CancelledError)):
        result = failure.result if isinstance(failure, (WorkflowPlanError, WorkflowPlanCancelledError)) else result
        result = replace(
            result,
            stop_error=None if isinstance(failure, asyncio.CancelledError) else error_info_for(failure),
            cancelled=isinstance(failure, asyncio.CancelledError),
        )
        outcome.fail(failure, handled=True)
    outcome.set_result(workflow_plan_payload(result, root), outcome.execution.exit_code or 0)
    outcome.render_text = lambda: _print_plan(result, root)
    outcome.text_handles_error = True


async def _plan_command(args: WorkflowArguments, overrides, policies, outcome: CommandOutcome) -> int:
    result = WorkflowPlanResult(args.url, args.download_scope or "updated")
    root = Path.cwd()
    try:
        root = _output_root(args) or root
        config, config_root, _, _ = _config_for(args, urlparse(args.url).hostname, rewrite_user_layers=False)
        root = _output_root(args) or resolve_paths(config)["downloads"]
        service = RuntimeComposer(
            config,
            config_root=config_root,
            plugin_root=_plugin_root(args, config),
            output_root=_output_root(args),
            plugin_verification_override=args.plugin_verification_override,
        )._compose_for_workflow_plan()
    except (Exception, asyncio.CancelledError) as error:
        outcome.fail(error, handled=True)
        _capture_plan(result, root, outcome)
        return outcome.exit_code
    async with outcome.closing(service.close, handled=True):
        try:
            result = await service.plan_workflow(
                WorkflowPlanRequest(
                    url=args.url,
                    download_scope=args.download_scope or "updated",
                    plugin_overrides=overrides,
                    fallback_override=_fallback(args),
                    plugin_id=args.plugin_id or args.force_plugin_id,
                    force_plugin=args.force_plugin_id is not None,
                    plugin_download_policy_overrides=policies,
                )
            )
        except (Exception, asyncio.CancelledError) as error:
            outcome.fail(error, handled=True)
        _capture_plan(result, root, outcome)
    if outcome.execution.failure is None and outcome.shutdown.failure is not None:
        close_error = outcome.shutdown.failure
        display_result = replace(
            result,
            stop_error=error_info_for(close_error) if isinstance(close_error, Exception) else None,
            cancelled=isinstance(close_error, asyncio.CancelledError),
        )
        outcome.render_text = lambda: _print_plan(display_result, root)
    return outcome.exit_code


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
