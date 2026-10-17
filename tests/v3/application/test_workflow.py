from __future__ import annotations

import asyncio
import json
import multiprocessing
import threading
from pathlib import Path

import pytest
from support.errors import expect_workflow_error
from support.synchronization import managed_task, wait_until_started
from support.workflow import FEED, A, B, Gallery, attach_transport, compose, snapshot

from image_downloader import (
    DownloadManifest,
    StorageSafetyError,
    UpdateCandidate,
    UpdateCheckRequest,
    UpdateStateError,
    WorkflowRequest,
)
from image_downloader.observability.events import EventName
from image_downloader.storage import FileSystem
from image_downloader.storage.workflow import WorkflowState, finish_transaction


def test_workflow_selection_failure_republication_and_mode_switch(tmp_path):
    async def scenario():
        gallery = Gallery()
        async with compose(tmp_path, gallery) as service:
            await attach_transport(service)
            await service.check_updates(UpdateCheckRequest(url=FEED))  # Listing does not initialize workflow history.
            gallery.fail = {B.url}
            first = await service.workflow(WorkflowRequest(url=FEED, workflow_retries=0))
            assert first.selected_urls == (A.url, B.url)
            assert [item.status for item in first.items] == ["success", "failed"]
            gallery.fail.clear()
            second = await service.workflow(WorkflowRequest(url=FEED, workflow_retries=0))
            assert second.selected_urls == (B.url,)
            assert second.items[0].reasons == ("unfinished",)
            assert (await service.workflow(WorkflowRequest(url=FEED, workflow_retries=0))).items == ()
            gallery.candidates = (B,)
            removed = await service.workflow(WorkflowRequest(url=FEED, workflow_retries=0))
            assert removed.items == ()
            assert [change.kind for change in removed.changes] == ["removed"]
            gallery.candidates = (A, B)
            assert (await service.workflow(WorkflowRequest(url=FEED, workflow_retries=0))).selected_urls == (A.url,)
            gallery.candidates = (A, UpdateCandidate(B.url, "b", "2"))
            changed = await service.workflow(WorkflowRequest(url=FEED, workflow_retries=0))
            assert changed.selected_urls == (B.url,)
            assert changed.items[0].reasons == ("changed",)
            assert (
                await service.workflow(WorkflowRequest(url=FEED, workflow_retries=0, download_scope="all"))
            ).selected_urls == (
                A.url,
                B.url,
            )
            assert (await service.workflow(WorkflowRequest(url=FEED, workflow_retries=0))).items == ()
            gallery.candidates = ()
            assert (await service.workflow(WorkflowRequest(url=FEED, workflow_retries=0))).items == ()
            assert gallery.checks == 10

    asyncio.run(scenario())


def test_duplicate_urls_are_fetched_once_and_all_identities_completed(tmp_path):
    async def scenario():
        gallery = Gallery()
        gallery.candidates = (A, UpdateCandidate(A.url, "another-id", "1"))
        async with compose(tmp_path, gallery) as service:
            await attach_transport(service)
            result = await service.workflow(WorkflowRequest(url=FEED, workflow_retries=0))
            assert result.selected_urls == (A.url,)
            assert gallery.seen == [A.url]
            assert (await service.workflow(WorkflowRequest(url=FEED, workflow_retries=0))).items == ()

    asyncio.run(scenario())


@pytest.mark.parametrize("policy", ["overwrite", "skip", "rename", "error"])
def test_workflow_existing_file_policy_and_forced_format(tmp_path, policy):
    async def scenario():
        gallery = Gallery()
        gallery.candidates = (A,)
        async with compose(tmp_path, gallery, existing_file=policy) as service:
            await attach_transport(service)
            first = await service.workflow(WorkflowRequest(url=FEED, workflow_retries=0, force_image_format="WEBP"))
            assert first.items[0].download.saved_files[0].endswith(".webp")
            second = await service.workflow(
                WorkflowRequest(url=FEED, workflow_retries=0, download_scope="all", force_image_format="WEBP")
            )
            item = second.items[0]
            if policy == "error":
                assert item.status in ("partial", "failed")
                assert (await service.workflow(WorkflowRequest(url=FEED, workflow_retries=0))).selected_urls == (A.url,)
            else:
                assert item.status == "success"
                if policy == "skip":
                    assert item.download.skipped_files
                if policy == "rename":
                    assert item.download.saved_files != first.items[0].download.saved_files
                assert (await service.workflow(WorkflowRequest(url=FEED, workflow_retries=0))).items == ()

    asyncio.run(scenario())


def test_partial_images_remain_unfinished(tmp_path):
    async def scenario():
        gallery = Gallery()
        async with compose(tmp_path, gallery) as service:
            await attach_transport(service, bad_url=B.url + ".png")
            result = await service.workflow(WorkflowRequest(url=FEED, workflow_retries=0))
            assert result.items[1].status == "partial"
            await attach_transport(service)
            assert (await service.workflow(WorkflowRequest(url=FEED, workflow_retries=0))).selected_urls == (B.url,)

    asyncio.run(scenario())


def test_fatal_stop_retains_success_and_unprocessed_targets(tmp_path, monkeypatch):
    async def scenario():
        gallery = Gallery()
        async with compose(tmp_path, gallery) as service:
            await attach_transport(service)
            original = service._downloads.execute

            async def run(request, *args):
                if request.url == B.url:
                    raise StorageSafetyError()
                return await original(request, *args)

            monkeypatch.setattr(service._downloads, "execute", run)
            with expect_workflow_error(StorageSafetyError) as captured:
                await service.workflow(WorkflowRequest(url=FEED, workflow_retries=0))
            assert captured.value.result.items[0].status == "success"
            monkeypatch.setattr(service._downloads, "execute", original)
            assert (await service.workflow(WorkflowRequest(url=FEED, workflow_retries=0))).selected_urls == (B.url,)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "document", ["broken", '{"schema_version": 2, "sources": {}}', '{"schema_version": 1, "sources": {"p": {"f": {}}}}']
)
def test_corrupt_workflow_state_stops_before_check_and_is_preserved(tmp_path, document):
    async def scenario():
        gallery = Gallery()
        async with compose(tmp_path, gallery) as service:
            service.state.filesystem.write_bytes_atomic("workflow.json", document.encode())
            with expect_workflow_error(UpdateStateError):
                await service.workflow(WorkflowRequest(url=FEED, workflow_retries=0))
            assert gallery.checks == 0
            assert service.state.filesystem.read_text("workflow.json") == document

    asyncio.run(scenario())


def test_state_separates_feeds_and_plugins_and_removes_absent_candidates(tmp_path):
    state = WorkflowState(FileSystem(tmp_path.resolve()))
    state.prepare("p", FEED, snapshot(A, B), "updated")
    state.complete("p", FEED, A.url)
    state.prepare("q", FEED, snapshot(B), "updated")
    state.prepare("p", FEED + "2", snapshot(B), "updated")
    _, selected = state.prepare("p", FEED, snapshot(A, B), "updated")
    assert tuple(selected) == (B.url,)
    state.prepare("p", FEED, snapshot(A), "updated")
    document = json.loads(state.filesystem.read_text("workflow.json"))
    assert FEED not in document["sources"]["p"]
    assert len(document["sources"]["p"]) == 2
    assert "q" in document["sources"]


def test_cancelled_workflow_keeps_completed_urls_and_releases_feed_lock(tmp_path, monkeypatch):
    async def scenario():
        gallery = Gallery()
        async with compose(tmp_path, gallery) as service:
            await attach_transport(service)
            original = service._downloads.execute
            started = asyncio.Event()

            async def run(request, *args):
                if request.url == B.url:
                    started.set()
                    await asyncio.Event().wait()
                return await original(request, *args)

            monkeypatch.setattr(service._downloads, "execute", run)
            async with managed_task(service.workflow(WorkflowRequest(url=FEED, workflow_retries=0))) as task:
                await wait_until_started(started, task)
                task.cancel()
                with pytest.raises(asyncio.CancelledError) as captured:
                    await task
                assert captured.value.result.cancelled
                assert [item.status for item in captured.value.result.items] == ["success", "unprocessed"]
                monkeypatch.setattr(service._downloads, "execute", original)
                assert (await service.workflow(WorkflowRequest(url=FEED, workflow_retries=0))).selected_urls == (B.url,)

    asyncio.run(scenario())


def test_same_feed_workflows_serialize_before_check(tmp_path):
    async def scenario():
        first, second = Gallery(), Gallery()
        started, release = asyncio.Event(), asyncio.Event()
        original = first.check_updates

        async def check(url, context):
            started.set()
            await release.wait()
            return await original(url, context)

        first.check_updates = check
        async with compose(tmp_path, first) as service1, compose(tmp_path, second) as service2:
            await attach_transport(service1)
            await attach_transport(service2)
            async with managed_task(service1.workflow(WorkflowRequest(url=FEED, workflow_retries=0))) as task1:
                await wait_until_started(started, task1)
                async with managed_task(service2.workflow(WorkflowRequest(url=FEED, workflow_retries=0))) as task2:
                    await asyncio.sleep(0.2)
                    assert second.checks == 0
                    release.set()
                    result1, result2 = await asyncio.wait_for(asyncio.gather(task1, task2), 15)
                    assert result1.selected_urls == (A.url, B.url)
                    assert result2.items == ()

    asyncio.run(scenario())


def test_cancellation_waits_for_started_transaction():
    async def scenario():
        started, release, finished = asyncio.Event(), threading.Event(), threading.Event()
        loop = asyncio.get_running_loop()

        def commit():
            loop.call_soon_threadsafe(started.set)
            assert release.wait(30)
            finished.set()

        async with managed_task(finish_transaction(commit)) as task:
            try:
                await wait_until_started(started, task)
                task.cancel()
                await asyncio.sleep(0.05)
                assert not task.done()
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert finished.is_set()
            finally:
                release.set()

    asyncio.run(scenario())


def _prepare_feed_process(root, feed, ready, start):
    state = WorkflowState(FileSystem(Path(root)))
    ready.set()
    assert start.wait(10)
    state.prepare("p", feed, snapshot(A), "updated")
    state.complete("p", feed, A.url)


def test_parallel_processes_preserve_both_workflow_feeds(tmp_path):
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    ready = [context.Event(), context.Event()]
    processes = [
        context.Process(target=_prepare_feed_process, args=(str(tmp_path), FEED + str(index), ready[index], start))
        for index in range(2)
    ]
    try:
        for process in processes:
            process.start()
        assert all(event.wait(10) for event in ready)
        start.set()
        for process in processes:
            process.join(10)
            assert process.exitcode == 0
        document = json.loads((tmp_path / "workflow.json").read_text())
        assert len(document["sources"]["p"]) == 2
        assert all(feed["records"]["id:a"]["completed"] for feed in document["sources"]["p"].values())
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(5)


def test_plugin_failure_notification_is_not_duplicated_and_selection_is_per_url(tmp_path):
    async def scenario():
        gallery = Gallery()
        gallery.fail = {A.url}
        async with compose(tmp_path, gallery) as service:
            await attach_transport(service)
            selections, failures = [], []
            record = service.registry.records["core.generic-html"]

            def select(url, **kwargs):
                selections.append((url, kwargs))
                return record, gallery

            service.registry.resolve = select
            service.events.on(EventName.PLUGIN_FAILED, failures.append)
            result = await service.workflow(
                WorkflowRequest(url=FEED, workflow_retries=0, plugin_id="chosen", force_plugin=True)
            )
            assert [url for url, _ in selections] == [FEED, A.url, B.url]
            assert all(kwargs["plugin_id"] == "chosen" and kwargs["force_plugin"] for _, kwargs in selections)
            assert len(failures) == 1
            assert result.items[1].status == "success"
            assert gallery.cleanups == 3

    asyncio.run(scenario())


def test_cleanup_failure_does_not_mark_candidate_completed(tmp_path):
    async def scenario():
        gallery = Gallery()
        gallery.candidates = (A,)

        def cleanup():
            if gallery.seen:
                raise RuntimeError("private cleanup failure")

        gallery.cleanup_after_use = cleanup
        async with compose(tmp_path, gallery) as service:
            await attach_transport(service)
            result = await service.workflow(WorkflowRequest(url=FEED, workflow_retries=0))
            assert result.items[0].status == "failed"
            gallery.cleanup_after_use = lambda: None
            assert (await service.workflow(WorkflowRequest(url=FEED, workflow_retries=0))).selected_urls == (A.url,)

    asyncio.run(scenario())


def test_permitted_empty_manifest_completes_candidate(tmp_path):
    async def scenario():
        gallery = Gallery()
        gallery.candidates = (A,)

        async def inspect(url, context):
            return DownloadManifest("empty", ())

        gallery.inspect = inspect
        async with compose(tmp_path, gallery, download={"allow_empty_chapter_manifest": True}) as service:
            result = await service.workflow(WorkflowRequest(url=FEED, workflow_retries=0))
            assert result.items[0].status == "success"
            assert result.items[0].download.chapters == ()
            assert (await service.workflow(WorkflowRequest(url=FEED, workflow_retries=0))).items == ()

    asyncio.run(scenario())


def test_workflow_state_failure_emits_storage_event_once(tmp_path):
    async def scenario():
        async with compose(tmp_path, Gallery()) as service:
            failures = []
            service.events.on(EventName.STORAGE_FAILED, failures.append)
            service.state.filesystem.write_bytes_atomic("workflow.json", b"broken")
            with expect_workflow_error(UpdateStateError):
                await service.workflow(WorkflowRequest(url=FEED, workflow_retries=0))
            assert len(failures) == 1
            assert failures[0].operation == "workflow"

    asyncio.run(scenario())
