"""Waiter interruptions retain their identity after owned work has finished."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from image_downloader.commands.outcome import CommandOutcome
from image_downloader.exceptions import UpdateStateError
from image_downloader.storage.filesystem import FileSystem
from image_downloader.storage.transactions import finish_task, settle_task
from image_downloader.storage.workflow import WorkflowState


@pytest.mark.parametrize("kind", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("job_fails", [False, True])
def test_waiter_interruption_settles_job_and_chains_its_failure(monkeypatch, kind, job_fails):
    async def scenario():
        release = asyncio.Event()
        lock = asyncio.Lock()
        events = []
        failure = ValueError("job failed")
        interruption = kind("waiter interrupted")
        original = asyncio.shield
        injected = False

        def shield(task):
            nonlocal injected
            if not injected:
                injected = True
                asyncio.get_running_loop().call_soon(release.set)
                raise interruption
            return original(task)

        async def job():
            async with lock:
                await release.wait()
                events.append("committed and registered")
                if job_fails:
                    raise failure
                return 42

        monkeypatch.setattr(asyncio, "shield", shield)
        with pytest.raises(kind) as caught:
            await finish_task(job())
        assert caught.value is interruption
        assert caught.value.__cause__ is (failure if job_fails else None)
        assert events == ["committed and registered"]
        assert not lock.locked()

    asyncio.run(scenario())


def test_interrupt_at_completion_is_distinct_from_the_jobs_own_exception(monkeypatch):
    async def scenario():
        original = asyncio.shield
        interruption = KeyboardInterrupt("after completion")

        async def shield(task):
            await original(task)
            raise interruption

        async def job():
            return "saved"

        monkeypatch.setattr(asyncio, "shield", shield)
        result = await settle_task(asyncio.create_task(job()))
        assert result.value == "saved"
        assert result.error is None
        assert result.interruption is interruption

    asyncio.run(scenario())


def test_job_exception_is_not_misclassified_as_waiter_interruption():
    async def scenario():
        failure = ValueError("job failed")

        async def job():
            raise failure

        result = await settle_task(asyncio.create_task(job()))
        assert result.error is failure
        assert result.interruption is None

    asyncio.run(scenario())


def test_first_waiter_interruption_wins_after_later_interruptions(monkeypatch):
    async def scenario():
        interruptions = [KeyboardInterrupt("first"), SystemExit("second")]
        first = interruptions[0]
        original = asyncio.shield

        def shield(task):
            if interruptions:
                raise interruptions.pop(0)
            return original(task)

        async def job():
            return "finished"

        monkeypatch.setattr(asyncio, "shield", shield)
        result = await settle_task(asyncio.create_task(job()))
        assert result.value == "finished"
        assert result.interruption is first

    asyncio.run(scenario())


@pytest.mark.parametrize("body_fails", [False, True])
def test_command_close_waiter_interruption_remains_primary(monkeypatch, body_fails):
    async def scenario():
        original = asyncio.shield
        interruption = KeyboardInterrupt("waiter")
        close_failure = ValueError("close failed")
        injected = False
        finished = []

        def shield(task):
            nonlocal injected
            if not injected:
                injected = True
                raise interruption
            return original(task)

        async def close():
            finished.append(True)
            raise close_failure

        monkeypatch.setattr(asyncio, "shield", shield)
        outcome = CommandOutcome("download", json_output=True)
        with pytest.raises(KeyboardInterrupt) as caught:
            async with outcome.closing(close):
                if body_fails:
                    raise ValueError("body failed")
        assert caught.value is interruption
        assert interruption.__cause__ is close_failure
        assert finished == [True]
        assert outcome.shutdown.failure is interruption

    asyncio.run(scenario())


@pytest.mark.parametrize("sources", ["{}", '{"test.review":{}}'])
def test_missing_workflow_source_is_classified_without_state_write(tmp_path, sources):
    filesystem = FileSystem(tmp_path.resolve())
    path = Path("workflow.json")
    filesystem.write_bytes_atomic(path, ('{"schema_version":1,"sources":' + sources + "}").encode())
    before = (tmp_path / path).read_bytes()
    with pytest.raises(UpdateStateError, match="workflow target source not found"):
        WorkflowState(filesystem).set_status(
            "test.review", "https://review.test/feed", "https://review.test/page", "completed"
        )
    assert (tmp_path / path).read_bytes() == before
