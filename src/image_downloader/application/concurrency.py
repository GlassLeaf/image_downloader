"""Bounded execution with deterministic failure and cancellation precedence."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar, cast

from ..exceptions import (
    AuthenticationError,
    ExistingFileConflictError,
    ImageProcessingError,
    PluginError,
    RequestError,
)
from ..storage.transactions import TaskSettlement, capture_result

_ResultT = TypeVar("_ResultT")


def _parallel_failure_priority(error: BaseException) -> int:
    """Preserve cancellation and operation-stopping failures over URL failures."""
    if not isinstance(error, Exception):
        return 0
    if isinstance(
        error,
        (AuthenticationError, PluginError, RequestError, ImageProcessingError, ExistingFileConflictError),
    ):
        return 2
    return 1


async def bounded(
    factories: Sequence[Callable[[], Awaitable[_ResultT]]],
    limit: int,
) -> list[_ResultT]:
    """Run at most ``limit`` jobs, never launching queued jobs after a failure."""
    results: list[_ResultT | None] = [None] * len(factories)
    pending = iter(enumerate(factories))
    active: dict[asyncio.Future[TaskSettlement[_ResultT]], int] = {}
    failures: list[tuple[int, BaseException]] = []

    def start_next() -> bool:
        try:
            index, factory = next(pending)
        except StopIteration:
            return False
        active[asyncio.ensure_future(capture_result(factory))] = index
        return True

    def collect(task: asyncio.Future[TaskSettlement[_ResultT]]) -> None:
        index = active.pop(task)
        try:
            outcome = task.result()
            if outcome.error is not None:
                failures.append((index, outcome.error))
            else:
                results[index] = outcome.value
        except BaseException as exc:
            failures.append((index, exc))

    for _ in range(min(limit, len(factories))):
        start_next()
    try:
        while active:
            done, _ = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                collect(task)
            if failures:
                # Started work is collected; queued work is intentionally never created.
                await asyncio.gather(*active, return_exceptions=True)
                for task in tuple(active):
                    collect(task)
                # Task completion order must not hide an operation-stopping error.
                raise min(failures, key=lambda failure: (_parallel_failure_priority(failure[1]), failure[0]))[1]
            for _ in range(len(done)):
                start_next()
    except asyncio.CancelledError:
        for task in active:
            task.cancel()
        await asyncio.gather(*active, return_exceptions=True)
        raise
    return [cast(_ResultT, result) for result in results]
