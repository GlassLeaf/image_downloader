"""Exception-safe lifecycle for operation-scoped diagnostics."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from types import TracebackType
from typing import Literal

from .diagnostic_safety import warn_diagnostic_failure
from .logging import DownloadLogger


class OperationDiagnosticsScope:
    """Flush operation observers and always detach Python log handlers.

    Cleanup failures remain diagnostics and never replace the operation result.
    """

    def __init__(
        self,
        logger: DownloadLogger,
        finalize_observers: Callable[[], Awaitable[None]],
    ) -> None:
        self._logger = logger
        self._finalize_observers = finalize_observers
        self._capture_started = False

    async def __aenter__(self) -> OperationDiagnosticsScope:
        return self

    def capture(self, namespaces: Iterable[str]) -> None:
        if self._capture_started:
            raise RuntimeError("operation diagnostics capture is already active")
        self._capture_started = True
        self._logger.begin_python_log_capture(namespaces)

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> Literal[False]:
        try:
            await self._finalize_observers()
        except Exception:
            warn_diagnostic_failure()
        try:
            if self._capture_started:
                await self._logger.flush_python_log_capture()
        except Exception:
            warn_diagnostic_failure()
        finally:
            if self._capture_started:
                try:
                    self._logger.end_python_log_capture()
                except Exception:
                    warn_diagnostic_failure()
        return False
