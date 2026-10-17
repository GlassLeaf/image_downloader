"""Await test milestones without confusing startup latency with operation deadlines."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from typing import Any, TypeVar

_T = TypeVar("_T")


@asynccontextmanager
async def managed_task(operation: Coroutine[Any, Any, _T]) -> AsyncIterator[asyncio.Task[_T]]:
    """Own a test task before waiting for any milestone or assertion."""
    task = asyncio.create_task(operation)
    try:
        yield task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def wait_until_started(started: asyncio.Event, operation: asyncio.Task, *, timeout: float = 30) -> None:
    milestone = asyncio.create_task(started.wait())
    try:
        done, _ = await asyncio.wait((milestone, operation), timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        if milestone in done:
            return
        if operation in done:
            await operation
            raise AssertionError("operation completed before reaching the expected test milestone")
        raise AssertionError("operation did not reach the expected test milestone before the startup guard")
    finally:
        milestone.cancel()
        await asyncio.gather(milestone, return_exceptions=True)
