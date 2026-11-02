"""Bounded, service-owned execution of synchronous plugin image transforms."""

from __future__ import annotations

import asyncio
import contextvars
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from types import TracebackType
from typing import TypeVar

from ..exceptions import PluginError
from ..storage.transactions import finish_task

_T = TypeVar("_T")


class TransformExecutor:
    """Share a bounded worker pool across all transforms in one service.

    Cancellation before admission does not submit work. Once submitted, work is
    settled before releasing admission or allowing plugin cleanup to proceed.
    """

    def __init__(self, max_workers: int = 2) -> None:
        if type(max_workers) is not int or not 1 <= max_workers <= 4:
            raise ValueError("max_workers must be an integer between 1 and 4")
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="image-transform")
        self._slots = asyncio.Semaphore(max_workers)
        self._closed = False
        self._owner_loop: asyncio.AbstractEventLoop | None = None
        self._close_task: asyncio.Task[None] | None = None

    async def run(self, callback: Callable[[], _T]) -> _T:
        """Run one synchronous callback with a copy of the caller's context."""
        self._bind_loop()
        self._ensure_open()
        async with self._slots:
            self._ensure_open()
            context = contextvars.copy_context()
            future = asyncio.get_running_loop().run_in_executor(self._pool, context.run, callback)
            return await finish_task(future)

    def _ensure_open(self) -> None:
        if self._closed:
            raise PluginError("transform executor is closed")

    def _bind_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._owner_loop is None:
            self._owner_loop = loop
        elif self._owner_loop is not loop:
            raise PluginError("transform executor must be used on its owning event loop")

    async def close(self) -> None:
        """Reject new work and finish admitted work; closing is idempotent."""
        self._bind_loop()
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(asyncio.to_thread(self._pool.shutdown, wait=True))
        await finish_task(self._close_task)

    async def __aenter__(self) -> TransformExecutor:
        self._ensure_open()
        return self

    async def __aexit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        await self.close()
