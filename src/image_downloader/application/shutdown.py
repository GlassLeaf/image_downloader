"""Settle owned resources and keep an operation's failure primary."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Protocol

from ..media.image_processor import ImageProcessor
from ..observability.diagnostic_safety import warn_diagnostic_failure
from ..observability.logging import DownloadLogger
from ..plugins.runtime import PluginRuntime
from ..transport.gateway import RequestGateway


class _Closable(Protocol):
    async def close(self) -> None: ...


def warn_shutdown_failure() -> None:
    try:
        sys.stderr.write("warning: service cleanup failed; original failure is unchanged\n")
    except BaseException:
        pass


async def close_service(service: _Closable, *, primary: BaseException | None = None) -> None:
    """Preserve the exception already leaving a library or CLI operation."""
    try:
        await service.close()
    except BaseException:
        if primary is None:
            raise
        warn_shutdown_failure()


async def _close_logger(logger: DownloadLogger) -> None:
    """Ordinary diagnostic failures never enter shutdown failure precedence."""
    try:
        await logger.close()
    except Exception:
        # Cancellation and fatal exceptions belong to the resource owner.
        warn_diagnostic_failure()


async def shutdown_resources(
    *,
    persist_cookies: Callable[[], Awaitable[None]] | None,
    gateway: RequestGateway,
    image_processor: ImageProcessor,
    registry: PluginRuntime,
    logger: DownloadLogger,
) -> BaseException | None:
    """Return failures as data so fatal exceptions never escape a child task.

    Every started close finishes before the next resource closes. The owning
    service shields this whole sequence, including the worker's shutdown thread.
    """
    async def close_image_processor() -> None:
        await asyncio.to_thread(image_processor.close)

    async def close_registry() -> None:
        registry.close()

    callbacks = ([persist_cookies] if persist_cookies is not None else []) + [
        gateway.close,
        close_image_processor,
        close_registry,
        partial(_close_logger, logger),
    ]
    failures: list[BaseException] = []
    for close in callbacks:
        try:
            await close()
        except BaseException as exc:
            failures.append(exc)
    if not failures:
        return None
    # Keep a fatal interruption over ordinary cleanup errors; otherwise retain
    # resource order. Caller cancellation is resolved after this result settles.
    primary = next((error for error in failures if not isinstance(error, Exception)), failures[0])
    for error in failures:
        if error is not primary:
            warn_shutdown_failure()
    return primary
