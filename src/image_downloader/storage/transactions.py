"""Finish started work before releasing resources, with explicit error precedence."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, TypeVar, cast

_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class TaskSettlement(Generic[_T]):
    value: _T | None
    error: BaseException | None
    cancellation: asyncio.CancelledError | None


async def capture_result(factory: Callable[[], Awaitable[_T]]) -> TaskSettlement[_T]:
    """Keep every job failure inside its task until its owner can handle it.

    SystemExit and KeyboardInterrupt escaping a child task stop the event loop
    before an awaiting owner can collect its siblings or preserve a primary error.
    """
    try:
        return TaskSettlement(await factory(), None, None)
    except BaseException as error:
        return TaskSettlement(None, error, None)


async def settle_task(task: asyncio.Task[_T]) -> TaskSettlement[_T]:
    """Observe cancellation but never abandon work that may hold a file lock."""
    cancellation = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            cancellation = exc
        except BaseException:
            break
    try:
        return TaskSettlement(task.result(), None, cancellation)
    except BaseException as error:
        return TaskSettlement(None, error, cancellation)


async def settle_operation(operation: Awaitable[_T]) -> TaskSettlement[_T]:
    """Start an operation with failures captured before its task is scheduled."""
    task = asyncio.create_task(capture_result(lambda: operation))
    settled = await settle_task(task)
    if settled.error is not None:
        return TaskSettlement(None, settled.error, settled.cancellation)
    outcome = cast(TaskSettlement[_T], settled.value)
    return TaskSettlement(outcome.value, outcome.error, settled.cancellation)


async def finish_task(operation: Awaitable[_T]) -> _T:
    """A transaction failure takes precedence over cancellation of its caller."""
    settled = await settle_operation(operation)
    if settled.error is not None:
        raise settled.error
    if settled.cancellation is not None:
        raise settled.cancellation
    return cast(_T, settled.value)


async def finish_transaction(function: Callable[[], _T]) -> _T:
    return await finish_task(asyncio.to_thread(function))
