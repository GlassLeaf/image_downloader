"""Best-effort history recording that never replaces a workflow outcome."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from ..configuration.models import AppConfig
from ..configuration.paths import resolve_paths
from ..models import WorkflowResult, WorkflowRunRecord
from ..privacy.log_safety import safe_url
from ..storage.filesystem import FileSystem
from ..storage.state import UpdateState
from ..storage.workflow_history import WorkflowHistoryStore
from .workflow_reporting import history_details

SAVE_WARNING = "workflow history could not be saved; execution result is unchanged"
SIZE_WARNING = "workflow history exceeds its size limit; consider pruning, increasing the limit or archiving history"


def warn_history(message: str) -> None:
    try:
        print(f"warning: {message}", file=sys.stderr)
    except Exception:
        pass


async def record_result(
    config: AppConfig,
    result: WorkflowResult,
    plugin_id: str | None,
    started_at: datetime,
    exit_code: int,
    output_root: Path,
) -> WorkflowResult:
    assert result.run_id is not None
    try:
        store = WorkflowHistoryStore(FileSystem(resolve_paths(config)["state"]), config.workflow_history)
        record = WorkflowRunRecord(
            result.run_id,
            plugin_id,
            UpdateState._source_key(result.source_url),
            safe_url(result.source_url),
            started_at,
            max(started_at, datetime.now(UTC)),
            exit_code,
            history_details(result, output_root),
        )
        oversized = False

        def commit() -> None:
            nonlocal oversized
            oversized = store.save(record)

        cancellation = await _settle(commit)
        if cancellation is not None:
            result = replace(result, cancelled=True, stop_error=None, history_saved=True)

            def finalize_cancel() -> None:
                nonlocal oversized
                oversized = store.revise(record.run_id, history_details(result, output_root), 130)

            try:
                await _settle(finalize_cancel)
            except (Exception, asyncio.CancelledError):
                result = replace(result, history_saved=False, history_warning=SAVE_WARNING)
                warn_history(SAVE_WARNING)
            if oversized and result.history_saved:
                warn_history(SIZE_WARNING)
                result = replace(result, history_warning=SIZE_WARNING)
            cancellation.__dict__["workflow_result"] = result
            raise cancellation
        if oversized:
            warn_history(SIZE_WARNING)
        return replace(result, history_saved=True, history_warning=SIZE_WARNING if oversized else None)
    except asyncio.CancelledError as exc:
        if not hasattr(exc, "workflow_result"):
            exc.__dict__["workflow_result"] = replace(
                result, cancelled=True, stop_error=None, history_saved=False, history_warning=SAVE_WARNING
            )
        raise
    except Exception:
        warn_history(SAVE_WARNING)
        return replace(result, history_saved=False, history_warning=SAVE_WARNING)


async def revise_result(config: AppConfig, result: WorkflowResult, exit_code: int, output_root: Path) -> WorkflowResult:
    if result.run_id is None or not result.history_saved:
        return result
    saved_run_id = result.run_id
    try:
        store = WorkflowHistoryStore(FileSystem(resolve_paths(config)["state"]), config.workflow_history)
        oversized = False

        def commit() -> None:
            nonlocal oversized
            oversized = store.revise(saved_run_id, history_details(result, output_root), exit_code)

        cancellation = await _settle(commit)
        if cancellation is not None:
            result = replace(result, cancelled=True, stop_error=None, history_saved=True)
            run_id = result.run_id
            assert run_id is not None

            def finalize_cancel() -> None:
                nonlocal oversized
                oversized = store.revise(run_id, history_details(result, output_root), 130)

            try:
                await _settle(finalize_cancel)
            except (Exception, asyncio.CancelledError):
                result = replace(result, history_saved=False, history_warning=SAVE_WARNING)
                warn_history(SAVE_WARNING)
            if oversized and result.history_saved:
                warn_history(SIZE_WARNING)
                result = replace(result, history_warning=SIZE_WARNING)
            cancellation.__dict__["workflow_result"] = result
            raise cancellation
        if oversized:
            warn_history(SIZE_WARNING)
        return replace(result, history_saved=True, history_warning=SIZE_WARNING if oversized else None)
    except asyncio.CancelledError as exc:
        if not hasattr(exc, "workflow_result"):
            exc.__dict__["workflow_result"] = replace(
                result, cancelled=True, stop_error=None, history_saved=False, history_warning=SAVE_WARNING
            )
        raise
    except Exception:
        warn_history(SAVE_WARNING)
        return replace(result, history_saved=False, history_warning=SAVE_WARNING)


async def _settle(function: Callable[[], None]) -> asyncio.CancelledError | None:
    """Finish a started write, preserving cancellation even if the write fails."""
    task = asyncio.create_task(asyncio.to_thread(function))
    cancellation = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            cancellation = exc
        except Exception:
            break
    try:
        task.result()
    except Exception:
        if cancellation is not None:
            warn_history(SAVE_WARNING)
            raise cancellation from None
        raise
    return cancellation
