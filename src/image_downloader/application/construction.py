"""Rollback resources that have not yet been used or transferred to a service."""

from __future__ import annotations

import asyncio
import sys
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from types import TracebackType
from typing import Literal, cast

_Resource = Literal["plugins", "logging", "http", "image"]


def _warn_cleanup_failure(resource: _Resource) -> None:
    # Construction errors can contain credentials. Never render either error.
    try:
        sys.stderr.write(f"warning: runtime initialization cleanup failed ({resource})\n")
    except BaseException:
        pass


@dataclass(frozen=True, slots=True)
class _Cleanup:
    resource: _Resource
    close: Callable[[], object]
    asynchronous: bool


async def _close_async(callbacks: list[_Cleanup]) -> None:
    for callback in callbacks:
        try:
            await cast(Awaitable[object], callback.close())
        except BaseException:
            _warn_cleanup_failure(callback.resource)


def _run_async_cleanup(callbacks: list[_Cleanup]) -> None:
    """Finish unused resources without running the caller's active event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(_close_async(callbacks))
        return

    failures: list[BaseException] = []
    finished = threading.Event()

    def run() -> None:
        try:
            asyncio.run(_close_async(callbacks))
        except BaseException as error:
            failures.append(error)
        finally:
            finished.set()

    worker = threading.Thread(target=run, name="image-downloader-initialization-cleanup")
    worker.start()
    while not finished.is_set():
        try:
            finished.wait()
        except BaseException:
            # Keep the original construction failure and finish the cleanup
            # thread even if waiting for it is interrupted a second time.
            for callback in callbacks:
                _warn_cleanup_failure(callback.resource)
    worker.join()
    if failures:
        raise failures[0]


class _ConstructionGuard:
    """Own construction resources until an explicit successful transfer."""

    def __init__(self) -> None:
        self._callbacks: list[_Cleanup] = []

    def __enter__(self) -> _ConstructionGuard:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        while self._callbacks:
            callback = self._callbacks.pop()
            if not callback.asynchronous:
                try:
                    callback.close()
                except BaseException:
                    _warn_cleanup_failure(callback.resource)
                continue
            batch = [callback]
            while self._callbacks and self._callbacks[-1].asynchronous:
                batch.append(self._callbacks.pop())
            try:
                _run_async_cleanup(batch)
            except BaseException:
                for item in batch:
                    _warn_cleanup_failure(item.resource)

    def callback(self, resource: _Resource, close: Callable[[], object]) -> None:
        self._callbacks.append(_Cleanup(resource, close, False))

    def async_callback(self, resource: _Resource, close: Callable[[], Awaitable[object]]) -> _Cleanup:
        callback = _Cleanup(resource, close, True)
        self._callbacks.append(callback)
        return callback

    def discard(self, callback: _Cleanup) -> None:
        self._callbacks = [item for item in self._callbacks if item is not callback]

    def release(self) -> None:
        self._callbacks.clear()
