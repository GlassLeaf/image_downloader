"""Collect command results without publishing completion before cleanup settles."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager, nullcontext, redirect_stdout
from dataclasses import dataclass, replace
from typing import Literal

from ..application.shutdown import warn_shutdown_failure
from ..storage.transactions import settle_operation
from .errors import _error_payload, _exit_status

PhaseStatus = Literal["success", "failed", "cancelled", "interrupted", "not_required", "not_started"]


@dataclass(frozen=True, slots=True)
class PhaseResult:
    status: PhaseStatus
    exit_code: int | None
    result: dict[str, object] | None = None
    failure: BaseException | None = None
    handled: bool = False


def failure_status(error: BaseException) -> int:
    return 130 if isinstance(error, asyncio.CancelledError) else _exit_status(error)


def json_mode(args: argparse.Namespace) -> bool:
    command = getattr(args, "command_handler", None)
    return bool(
        getattr(args, "json_output", False)
        or command == "inspect"
        or (command == "download" and getattr(args, "inspect_only", False))
    )


class CommandOutcome:
    """Own presentation data; resource ownership remains with each command."""

    def __init__(self, operation: str, *, json_output: bool, pretty: bool = False) -> None:
        self.operation = operation
        self.json_output = json_output
        self.pretty = pretty
        self.execution = PhaseResult("success", 0)
        self.shutdown = PhaseResult("not_started", None)
        self.render_text: Callable[[], None] | None = None
        self.text_handles_error = False

    def diagnostics(self):
        return redirect_stdout(sys.stderr) if self.json_output else nullcontext()

    def no_shutdown(self) -> None:
        self.shutdown = PhaseResult("not_required", None)

    def expect_shutdown(self) -> None:
        self.shutdown = PhaseResult("not_started", None)

    def set_result(
        self, payload: Mapping[str, object], status: int = 0, *, text: Callable[[], None] | None = None
    ) -> int:
        # Prepare before close so formatting failures retain operation precedence.
        if self.json_output:
            json.dumps(payload, ensure_ascii=False)
        self.execution = replace(
            self.execution,
            result=dict(payload),
            exit_code=status,
            status="cancelled" if status == 130 else "success" if status == 0 else "failed",
        )
        self.render_text = text
        return status

    def fail(self, error: BaseException, *, handled: bool = False, status: int | None = None) -> None:
        if self.execution.failure is not None and self.execution.failure is not error:
            return
        self.execution = replace(
            self.execution,
            failure=error,
            handled=handled,
            status="cancelled" if isinstance(error, asyncio.CancelledError) else "failed",
            exit_code=failure_status(error) if status is None else status,
        )

    def _closed(self, error: BaseException | None, *, handled: bool) -> None:
        if error is None:
            self.shutdown = PhaseResult("success", 0)
            return
        cancelled = isinstance(error, asyncio.CancelledError)
        fatal = not isinstance(error, (Exception, asyncio.CancelledError))
        self.shutdown = PhaseResult(
            "interrupted" if fatal else "cancelled" if cancelled else "failed",
            None if fatal else failure_status(error),
            failure=error,
            handled=handled,
        )
        if self.execution.failure is not None:
            warn_shutdown_failure()

    @asynccontextmanager
    async def closing(self, close: Callable[[], Awaitable[None]], *, handled: bool = False) -> AsyncIterator[None]:
        try:
            yield
        except BaseException as error:
            self.fail(error)
        finally:
            # Shield cleanup only: the command body must remain cancellable.
            settled = await settle_operation(close())
            self._closed(settled.error if settled.error is not None else settled.cancellation, handled=handled)

    @contextmanager
    def closing_sync(self, close: Callable[[], None]) -> Iterator[None]:
        try:
            yield
        except BaseException as error:
            self.fail(error)
        finally:
            try:
                close()
            except BaseException as error:
                self._closed(error, handled=False)
            else:
                self._closed(None, handled=False)

    @property
    def primary(self) -> BaseException | None:
        return self.execution.failure if self.execution.failure is not None else self.shutdown.failure

    @property
    def exit_code(self) -> int:
        phase = self.execution if self.execution.failure is not None else self.shutdown
        if phase.failure is not None:
            return phase.exit_code if phase.exit_code is not None else failure_status(phase.failure)
        return self.execution.exit_code or 0

    def _phase_payload(self, phase: PhaseResult) -> dict[str, object]:
        return {
            "status": phase.status,
            "exit_code": phase.exit_code,
            "result": phase.result,
            "error": _error_payload(phase.failure, operation=self.operation)
            if isinstance(phase.failure, Exception)
            else None,
        }

    def payload(self) -> dict[str, object]:
        return {
            "operation": self.operation,
            "exit_code": self.exit_code,
            "execution": self._phase_payload(self.execution),
            "shutdown": self._phase_payload(self.shutdown),
        }

    def finish(self, *, propagate: bool = False) -> int:
        primary = self.primary
        phase = self.execution if self.execution.failure is not None else self.shutdown
        if primary is not None:
            fatal = not isinstance(primary, (Exception, asyncio.CancelledError))
            if fatal or (propagate and not phase.handled):
                raise primary
        # Keep writes outside the process error handler: a broken output stream
        # must never trigger a second JSON write.
        if self.json_output:
            print(json.dumps(self.payload(), ensure_ascii=False, indent=2 if self.pretty else None))
        else:
            if self.render_text is not None:
                self.render_text()
            if isinstance(primary, Exception) and not self.text_handles_error:
                error = _error_payload(primary, operation=self.operation)
                print(f"error [{error['code']}]: {error['message']}", file=sys.stderr)
        return self.exit_code
