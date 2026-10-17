"""Cancellation must release operation diagnostics before the service is reused."""

from __future__ import annotations

import asyncio
import logging

import pytest
from support.synchronization import wait_until_started

from image_downloader.observability.logging import DownloadLogger
from image_downloader.observability.scope import OperationDiagnosticsScope


@pytest.mark.parametrize("stage", ["observers", "python_logs"])
def test_cancelled_finalization_detaches_capture_and_allows_another_operation(monkeypatch, stage):
    async def scenario():
        logger = DownloadLogger([])
        namespace = "review.cancellation.plugin"
        plugin_logger = logging.getLogger(namespace)
        original_handlers = tuple(plugin_logger.handlers)
        started = asyncio.Event()

        async def flush_observers():
            if stage == "observers" and not started.is_set():
                started.set()
                await asyncio.Event().wait()

        async def flush_logs():
            if stage == "python_logs" and not started.is_set():
                started.set()
                await asyncio.Event().wait()

        monkeypatch.setattr(logger, "flush_python_log_capture", flush_logs)

        async def operation():
            async with OperationDiagnosticsScope(logger, flush_observers) as scope:
                scope.capture((namespace,))

        task = asyncio.create_task(operation())
        try:
            await wait_until_started(started, task)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert tuple(plugin_logger.handlers) == original_handlers
            # This is a different task/context from the cancelled operation.
            logger.begin_python_log_capture((namespace,))
            logger.end_python_log_capture()
            assert tuple(plugin_logger.handlers) == original_handlers
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await logger.close()

    asyncio.run(scenario())
