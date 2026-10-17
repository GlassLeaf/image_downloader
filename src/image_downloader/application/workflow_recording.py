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
from ..exceptions import WorkflowCancelledError
from ..models import WorkflowResult, WorkflowRunRecord
from ..privacy.log_safety import safe_url
from ..storage.filesystem import FileSystem
from ..storage.source_identity import source_key
from ..storage.transactions import settle_operation
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
            source_key(result.source_url),
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
            raise WorkflowCancelledError(result) from cancellation
        if oversized:
            warn_history(SIZE_WARNING)
        return replace(result, history_saved=True, history_warning=SIZE_WARNING if oversized else None)
    except asyncio.CancelledError as exc:
        if isinstance(exc, WorkflowCancelledError):
            raise
        raise WorkflowCancelledError(
            replace(
                result,
                cancelled=True,
                stop_error=None,
                history_saved=False,
                history_warning=SAVE_WARNING,
            )
        ) from exc
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
            raise WorkflowCancelledError(result) from cancellation
        if oversized:
            warn_history(SIZE_WARNING)
        return replace(result, history_saved=True, history_warning=SIZE_WARNING if oversized else None)
    except asyncio.CancelledError as exc:
        if isinstance(exc, WorkflowCancelledError):
            raise
        raise WorkflowCancelledError(
            replace(
                result,
                cancelled=True,
                stop_error=None,
                history_saved=False,
                history_warning=SAVE_WARNING,
            )
        ) from exc
    except Exception:
        warn_history(SAVE_WARNING)
        return replace(result, history_saved=False, history_warning=SAVE_WARNING)


async def _settle(function: Callable[[], None]) -> asyncio.CancelledError | None:
    """History keeps cancellation primary even when the recording write fails."""
    settled = await settle_operation(asyncio.to_thread(function))
    if settled.error is not None:
        if settled.cancellation is not None and isinstance(settled.error, Exception):
            warn_history(SAVE_WARNING)
            raise settled.cancellation from None
        raise settled.error
    return settled.cancellation
