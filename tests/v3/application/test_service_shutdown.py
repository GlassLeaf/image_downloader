"""Resource settlement and exception precedence at service ownership boundaries."""

from __future__ import annotations

import asyncio
import threading

import pytest
from support.synchronization import managed_task, wait_until_started
from support.workflow import FEED, Gallery, compose

from image_downloader import (
    AuthenticationError,
    InspectRequest,
    WorkflowCancelledError,
    WorkflowExecutionError,
    WorkflowPlanCancelledError,
    WorkflowPlanError,
)
from image_downloader.models import WorkflowPlanResult, WorkflowResult


def primary_error(kind):
    result = WorkflowResult(FEED, "updated", None, (), ())
    plan = WorkflowPlanResult(FEED, "updated")
    cause = AuthenticationError("private primary")
    return {
        "operation": cause,
        "workflow": WorkflowExecutionError(result, cause),
        "plan": WorkflowPlanError(plan, cause),
        "cancel": WorkflowCancelledError(result),
        "plan_cancel": WorkflowPlanCancelledError(plan),
        "interrupt": KeyboardInterrupt(),
        "exit": SystemExit(),
    }[kind]


@pytest.mark.parametrize("kind", ["operation", "workflow", "plan", "cancel", "plan_cancel", "interrupt", "exit"])
@pytest.mark.parametrize("stage", ["gateway", "registry"])
def test_context_exit_keeps_primary_exception_and_partial_result(tmp_path, monkeypatch, capsys, kind, stage):
    async def scenario():
        service = compose(tmp_path, Gallery())
        primary = primary_error(kind)
        resource = getattr(service, stage)
        original = resource.close
        if stage == "gateway":

            async def close():
                await original()
                raise RuntimeError("token=secondary-secret")

        else:

            def close():
                original()
                raise RuntimeError("token=secondary-secret")

        monkeypatch.setattr(resource, "close", close)
        with pytest.raises(type(primary)) as caught:
            async with service:
                raise primary
        assert caught.value is primary
        if hasattr(primary, "result"):
            assert caught.value.result is primary.result
        if hasattr(primary, "cause"):
            assert primary.__cause__ is primary.cause
        assert service.gateway.client.is_closed
        assert service.image_processor._closed
        assert not service._operation_lock.locked()
        await service.close()

    asyncio.run(scenario())
    diagnostic = capsys.readouterr().err
    assert "service cleanup failed" in diagnostic
    assert "secondary-secret" not in diagnostic


@pytest.mark.parametrize("stage", ["cookies", "gateway", "worker"])
@pytest.mark.parametrize("repeat_cancel", [False, True])
def test_cancelled_close_settles_resources_and_concurrent_waiters(
    tmp_path, monkeypatch, stage, repeat_cancel
):
    async def scenario():
        service = compose(tmp_path, Gallery())
        started, release = asyncio.Event(), asyncio.Event()
        worker_release = threading.Event()
        loop = asyncio.get_running_loop()
        order = []
        cookie_close = service._persist_cookies
        gateway_close = service.gateway.close
        worker_close = service.image_processor.close
        registry_close = service.registry.close
        logger_close = service.logger.close

        async def cookies():
            order.append("cookies")
            if stage == "cookies":
                started.set()
                await release.wait()
            await cookie_close()

        async def gateway():
            order.append("gateway")
            if stage == "gateway":
                started.set()
                await release.wait()
            await gateway_close()

        def worker():
            order.append("worker")
            if stage == "worker":
                loop.call_soon_threadsafe(started.set)
                assert worker_release.wait(30)
            worker_close()

        def registry():
            order.append("registry")
            registry_close()

        async def logger():
            order.append("logger")
            await logger_close()

        monkeypatch.setattr(service, "_persist_cookies", cookies)
        monkeypatch.setattr(service.gateway, "close", gateway)
        monkeypatch.setattr(service.image_processor, "close", worker)
        monkeypatch.setattr(service.registry, "close", registry)
        monkeypatch.setattr(service.logger, "close", logger)
        async with managed_task(service.close()) as first:
            try:
                await wait_until_started(started, first)
                async with managed_task(service.close()) as second:
                    try:
                        first.cancel()
                        await asyncio.sleep(0)
                        if repeat_cancel:
                            first.cancel()
                            await asyncio.sleep(0)
                        assert not first.done() and not second.done()
                        assert service._operation_lock.locked()
                        assert "registry" not in order
                    finally:
                        release.set()
                        worker_release.set()
                    with pytest.raises(asyncio.CancelledError):
                        await first
                    await second
            finally:
                release.set()
                worker_release.set()
        assert order == ["cookies", "gateway", "worker", "registry", "logger"]
        assert service._close_task.done()
        assert not service._operation_lock.locked()
        await service.close()
        assert len(order) == 5
        with pytest.raises(RuntimeError, match="closed"):
            await service.inspect(InspectRequest(url=FEED))

    asyncio.run(scenario())


@pytest.mark.parametrize("fatal", [False, True])
def test_shutdown_runs_all_resources_and_preserves_failure_priority(tmp_path, monkeypatch, fatal):
    async def scenario():
        service = compose(tmp_path, Gallery())
        first = AuthenticationError("first failure")
        second = KeyboardInterrupt() if fatal else RuntimeError("second failure")
        gateway_close, registry_close = service.gateway.close, service.registry.close

        async def cookies():
            raise first

        async def gateway():
            await gateway_close()
            raise RuntimeError("gateway failure")

        def registry():
            registry_close()
            raise second

        monkeypatch.setattr(service, "_persist_cookies", cookies)
        monkeypatch.setattr(service.gateway, "close", gateway)
        monkeypatch.setattr(service.registry, "close", registry)
        expected = second if fatal else first
        with pytest.raises(type(expected)) as caught:
            await service.close()
        assert caught.value is expected
        assert service.gateway.client.is_closed and service.image_processor._closed
        await service.close()

    asyncio.run(scenario())


def test_shutdown_failure_takes_precedence_over_waiter_cancellation(tmp_path, monkeypatch):
    async def scenario():
        service = compose(tmp_path, Gallery())
        started, release = asyncio.Event(), asyncio.Event()
        original = service.gateway.close
        failure = RuntimeError("close failed")

        async def close():
            started.set()
            await release.wait()
            await original()
            raise failure

        monkeypatch.setattr(service.gateway, "close", close)
        async with managed_task(service.close()) as task:
            try:
                await wait_until_started(started, task)
                task.cancel()
            finally:
                release.set()
            with pytest.raises(RuntimeError) as caught:
                await task
            assert caught.value is failure

    asyncio.run(scenario())


def test_close_waits_for_active_operation_before_closing_resources(tmp_path, monkeypatch):
    async def scenario():
        service = compose(tmp_path, Gallery())
        started, release = asyncio.Event(), asyncio.Event()

        async def inspect(_request):
            started.set()
            await release.wait()
            assert not service.gateway.client.is_closed

        monkeypatch.setattr(service._inspection, "execute", inspect)
        async with managed_task(service.inspect(InspectRequest(url=FEED))) as operation:
            try:
                await wait_until_started(started, operation)
                async with managed_task(service.close()) as closing:
                    try:
                        await asyncio.sleep(0)
                        assert not closing.done()
                        assert not service.gateway.client.is_closed
                    finally:
                        release.set()
                    await operation
                    await closing
            finally:
                release.set()
        assert service.gateway.client.is_closed

    asyncio.run(scenario())


def test_logger_close_failure_stays_diagnostic(tmp_path, monkeypatch, capsys):
    async def scenario():
        service = compose(tmp_path, Gallery())
        original = service.logger.close

        async def close():
            await original()
            raise RuntimeError("token=logger-secret")

        monkeypatch.setattr(service.logger, "close", close)
        await service.close()
        assert service.gateway.client.is_closed and service.image_processor._closed

    asyncio.run(scenario())
    output = capsys.readouterr()
    assert "diagnostic logging failed" in output.err
    assert "logger-secret" not in output.err
