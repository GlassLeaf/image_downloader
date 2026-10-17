"""Settle workflow history and progress before exposing completion or interruption."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from ..configuration.models import AppConfig
from ..exceptions import WorkflowCancelledError, WorkflowExecutionError
from ..models import WorkflowResult
from ..storage.workflow_logging import WorkflowLogWriter
from .workflow_recording import _settle, record_result, revise_result
from .workflow_reporting import stopped_status, workflow_outcome, workflow_status
from .workflow_runtime import WorkflowRuntime

if TYPE_CHECKING:
    from .workflow import _WorkflowExecution


class WorkflowFinalizer:
    def __init__(self, runtime: WorkflowRuntime) -> None:
        self.runtime = runtime

    async def finish(self, execution: _WorkflowExecution, failure: BaseException | None) -> WorkflowResult:
        runtime = self.runtime
        result = execution.result(failure)
        status = stopped_status(failure) if failure is not None and not result.timed_out else workflow_status(result)
        try:
            result = await record_result(
                runtime.config,
                result,
                execution.feed_plugin_id,
                execution.started_at,
                status,
                runtime.outputs.root,
            )
        except asyncio.CancelledError as exc:
            result = (
                exc.result
                if isinstance(exc, WorkflowCancelledError)
                else replace(
                    result,
                    cancelled=True,
                    stop_error=None,
                    history_saved=False,
                    history_warning="workflow history could not be saved; execution result is unchanged",
                )
            )
            failure = exc
        # History persistence and dedicated progress recording are independent.
        cancellation = await _settle(
            partial(
                execution.log.finish,
                result,
                stopped_status(failure) if failure is not None and not result.timed_out else workflow_status(result),
                workflow_outcome(result),
            )
        )
        if cancellation is not None:
            failure = cancellation
            result = replace(result, cancelled=True, stop_error=None)
            try:
                result = await revise_result(runtime.config, result, 130, runtime.outputs.root)
            except asyncio.CancelledError as exc:
                result = exc.result if isinstance(exc, WorkflowCancelledError) else result
            await _settle(partial(execution.log.finish, result, 130, workflow_outcome(result), correction=True))
        result = replace(result, workflow_log=execution.log.result)
        if failure is not None:
            if isinstance(failure, asyncio.CancelledError):
                raise WorkflowCancelledError(result) from failure
            if isinstance(failure, Exception):
                raise WorkflowExecutionError(result, failure) from failure
            raise failure
        return result


async def revise_after_close(
    config: AppConfig, result: WorkflowResult, status: int, root: Path
) -> tuple[WorkflowResult, int]:
    try:
        result = await revise_result(config, result, status, root)
    except asyncio.CancelledError as exc:
        result, status = (
            (exc.result if isinstance(exc, WorkflowCancelledError) else replace(result, cancelled=True)),
            130,
        )
    writer = WorkflowLogWriter.resume(result)
    if writer is not None:
        cancellation = await _settle(partial(writer.finish, result, status, workflow_outcome(result), correction=True))
        if cancellation is not None:
            result, status = replace(result, cancelled=True, stop_error=None), 130
            try:
                result = await revise_result(config, result, status, root)
            except asyncio.CancelledError as exc:
                result = exc.result if isinstance(exc, WorkflowCancelledError) else result
            await _settle(partial(writer.finish, result, status, workflow_outcome(result), correction=True))
        result = replace(result, workflow_log=writer.result)
    return result, status
