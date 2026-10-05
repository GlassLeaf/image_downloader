from __future__ import annotations

import asyncio
import json
import multiprocessing
import threading
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_workflow import FEED, A, B, Gallery, attach_transport, compose
from test_workflow_retry import RetryGallery, transport

from image_downloader import PluginError, UpdateCandidate, WorkflowPlanLogError, WorkflowRetryTimeoutError
from image_downloader.commands.workflow import _revise_after_close, workflow_payload
from image_downloader.config import WorkflowLogging, resolve_paths
from image_downloader.exceptions import StorageError, error_info_for
from image_downloader.storage.filesystem import FileSystem
from image_downloader.storage.workflow_logging import WorkflowLogWriter


def _write_plan_process(root, run_id):
    writer = WorkflowLogWriter(Path(root), run_id, datetime(2026, 10, 5, tzinfo=UTC), False)
    writer.plan(0, {"source_url": FEED, "selected_urls": [A.url, B.url]})


def test_processes_publish_separate_complete_plans(tmp_path):
    context = multiprocessing.get_context("spawn")
    processes = [context.Process(target=_write_plan_process, args=(str(tmp_path.resolve()), str(i))) for i in range(2)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(30)
        assert process.exitcode == 0
    documents = [json.loads(file.read_text(encoding="utf-8")) for file in tmp_path.glob("*.plan.json")]
    assert {document["run_id"] for document in documents} == {"0", "1"}
    assert all(document["selected_urls"] == [A.url, B.url] for document in documents)


def events(result):
    return [json.loads(line) for line in Path(result.workflow_log.jsonl_path).read_text(encoding="utf-8").splitlines()]


def plans(result):
    return [json.loads(Path(path).read_text(encoding="utf-8")) for path in result.workflow_log.plan_files]


def test_plan_is_complete_before_first_manifest_and_raw_urls_are_dedicated(tmp_path):
    raw = "https://example.test/a?token=private-token"

    class Checked(Gallery):
        candidates = (UpdateCandidate(raw, "a", "1"), B, UpdateCandidate(raw, "duplicate", "1"))

        async def inspect(self, url, context):
            files = list((resolve_paths(service.config)["logs"] / "workflow").glob("*.plan.json"))
            document = json.loads(files[0].read_text(encoding="utf-8"))
            assert [item["url"] for item in document["selected_urls"]] == [raw, B.url]
            assert document["candidates"] == 3
            return await super().inspect(url, context)

    async def scenario():
        nonlocal service
        gallery = Checked()
        async with compose(tmp_path, gallery) as service:
            await attach_transport(service)
            result = await service.workflow(FEED, workflow_retries=0)
            assert gallery.seen == [raw, B.url]
            plan = plans(result)[0]
            assert plan["workflow_history_absent"] is True
            assert plan["selected_url_count"] == 2
            assert plan["run_id"] == result.run_id
            assert (plan["round_number"], plan["run_number"]) == (0, 1)
            assert "private-token" in Path(result.workflow_log.jsonl_path).read_text(encoding="utf-8")
            assert "private-token" not in json.dumps(workflow_payload(result, service.outputs.root))
            public_log = workflow_payload(
                result, service.outputs.root, log_root=resolve_paths(service.config)["profile"]
            )["workflow_log"]
            assert public_log["plan_files"][0].startswith("logs/workflow/")
            assert public_log["jsonl_path"].startswith("logs/workflow/")
            records = events(result)
            assert records[0]["event"] == "run_started"
            assert records[-1]["event"] == "run_finished"
            assert records[-1]["exit_code"] == 0
            assert [r["event_number"] for r in records] == list(range(1, len(records) + 1))
            text = Path(result.workflow_log.text_path).read_text(encoding="utf-8").splitlines()
            assert len(text) == len(records)
            assert all(f"#{record['event_number']} " in line for record, line in zip(records, text, strict=True))
            assert result.workflow_log.jsonl_saved and result.workflow_log.text_saved

    service = None
    asyncio.run(scenario())


@pytest.mark.parametrize("failing_round", [1, 2])
def test_plan_failure_prevents_every_download_in_that_round(tmp_path, monkeypatch, failing_round):
    original = FileSystem.write_bytes_atomic_new

    def fail(self, relative, data):
        if str(relative).endswith(f"_run{failing_round}.plan.json"):
            raise OSError("secret disk failure")
        return original(self, relative, data)

    monkeypatch.setattr(FileSystem, "write_bytes_atomic_new", fail)

    async def scenario():
        gallery = RetryGallery()
        async with compose(tmp_path, gallery, network={"max_attempts": 1}) as service:
            await transport(service, gallery)
            with pytest.raises(WorkflowPlanLogError) as captured:
                await service.workflow(FEED, workflow_retry_delay=0)
            result = captured.value.workflow_result
            assert result.stop_error.code == "workflow_plan_log_error"
            assert len(gallery.seen) == failing_round - 1
            assert len(result.workflow_log.plan_files) == failing_round - 1
            assert gallery.checks == failing_round
            assert events(result)[-1]["exit_code"] == 1
            assert "secret disk failure" not in json.dumps(events(result))
            assert result.history_saved

    asyncio.run(scenario())


def test_retry_round_plan_is_new_and_success_is_retained(tmp_path):
    async def scenario():
        gallery = RetryGallery()
        async with compose(tmp_path, gallery, network={"max_attempts": 1}) as service:
            await transport(service, gallery)
            result = await service.workflow(FEED, workflow_retry_delay=0)
            initial, retry = plans(result)
            assert initial["workflow_history_absent"] is True
            assert retry["workflow_history_absent"] is False
            assert initial["selected_urls"][0]["reasons"] == ["added"]
            assert retry["selected_urls"][0]["reasons"] == ["unfinished"]
            assert retry["selected_urls"][0]["retry"] is True
            assert initial["run_number"] == 1 and retry["run_number"] == 2
            assert result.items[0].status == "success"
            assert len(result.items[0].download.saved_files) == 2
            records = events(result)
            assert any(r["event"] == "retry_wait" for r in records)
            assert [r["status"] for r in records if r["event"] == "url_finished"] == ["partial", "success"]

    asyncio.run(scenario())


def test_empty_unchanged_all_removed_republished_plans(tmp_path):
    async def scenario():
        gallery = Gallery()
        async with compose(tmp_path, gallery) as service:
            await attach_transport(service)
            first = await service.workflow(FEED, workflow_retries=0)
            first_bytes = Path(first.workflow_log.plan_files[0]).read_bytes()
            unchanged = await service.workflow(FEED, workflow_retries=0)
            assert plans(unchanged)[0]["selected_urls"] == []
            assert not plans(unchanged)[0]["workflow_history_absent"]
            all_result = await service.workflow(FEED, download_scope="all", workflow_retries=0)
            assert all(p["reasons"] == ["all"] for p in plans(all_result)[0]["selected_urls"])
            gallery.candidates = ()
            empty = await service.workflow(FEED, workflow_retries=0)
            assert plans(empty)[0]["candidates"] == 0
            assert [c["kind"] for c in plans(empty)[0]["changes"]] == ["removed", "removed"]
            assert [r["url"] for r in events(empty) if r["event"] == "target_removed"] == [A.url, B.url]
            gallery.candidates = (A,)
            published = await service.workflow(FEED, workflow_retries=0)
            assert plans(published)[0]["selected_urls"][0]["reasons"] == ["added"]
            gallery.candidates = (UpdateCandidate(A.url, "a", "2"),)
            changed = await service.workflow(FEED, workflow_retries=0)
            assert plans(changed)[0]["selected_urls"][0]["reasons"] == ["changed"]
            assert Path(first.workflow_log.plan_files[0]).read_bytes() == first_bytes

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["jsonl", "text"])
@pytest.mark.parametrize("stage", ["create", "append"])
def test_progress_sinks_fail_independently_and_downloads_continue(tmp_path, monkeypatch, kind, stage):
    suffix = ".jsonl" if kind == "jsonl" else ".log"
    method = "write_bytes_atomic_new" if stage == "create" else "open_text_append"
    original = getattr(FileSystem, method)
    failures = []

    def fail(self, relative, *args, **kwargs):
        if self.root.name == "workflow" and str(relative).endswith(suffix):
            failures.append(str(relative))
            raise OSError("private disk message")
        return original(self, relative, *args, **kwargs)

    monkeypatch.setattr(FileSystem, method, fail)

    async def scenario():
        async with compose(tmp_path, Gallery()) as service:
            await attach_transport(service)
            result = await service.workflow(FEED, workflow_retries=0)
            assert [item.status for item in result.items] == ["success", "success"]
            summary = result.workflow_log
            assert getattr(summary, kind + "_saved") is False
            assert getattr(summary, ("text" if kind == "jsonl" else "jsonl") + "_saved") is True
            assert len(failures) == 1
            assert len(summary.warnings) == 1
            assert "private disk message" not in summary.warnings[0]
            assert len(summary.plan_files) == 1
            assert result.history_saved
            assert workflow_payload(result, service.outputs.root)["workflow_log"][kind + "_saved"] is False

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "configured,override,enabled", [(True, None, True), (False, None, False), (False, True, True), (True, False, False)]
)
def test_configuration_and_api_override_do_not_disable_plans(tmp_path, configured, override, enabled):
    async def scenario():
        async with compose(tmp_path, Gallery()) as service:
            service.config = service.config.model_copy(
                update={"workflow_logging": WorkflowLogging(progress_enabled=configured)}
            )
            await attach_transport(service)
            result = await service.workflow(FEED, workflow_retries=0, workflow_progress_log=override)
            assert len(result.workflow_log.plan_files) == 1
            assert result.workflow_log.progress_enabled is enabled
            if not enabled:
                assert result.workflow_log.jsonl_path is None
                assert result.workflow_log.text_saved is None
                assert not list(Path(result.workflow_log.plan_files[0]).parent.glob("*.jsonl"))

    asyncio.run(scenario())


def test_dry_run_and_unconfirmed_check_create_no_plans(tmp_path):
    class Fails(Gallery):
        async def check_updates(self, url, context):
            raise PluginError("secret")

    async def scenario():
        async with compose(tmp_path, Gallery()) as service:
            await service.plan_workflow(FEED)
            assert not (resolve_paths(service.config)["logs"] / "workflow").exists()
        async with compose(tmp_path, Fails()) as service:
            with pytest.raises(PluginError) as captured:
                await service.workflow(FEED)
            assert captured.value.workflow_result.workflow_log.plan_files == ()

    asyncio.run(scenario())


def test_atomic_plan_collision_and_newline_safe_progress(tmp_path):
    writer = WorkflowLogWriter(tmp_path.resolve(), "test-run", datetime.now(UTC), True)
    raw = "https://example.test/path\nforged-event\rtest\u0085next\u2028next\u2029next"
    writer.start({"source_url": raw})
    writer.plan(0, {"selected_urls": [raw]})
    original = Path(writer.result.plan_files[0]).read_bytes()
    with pytest.raises(WorkflowPlanLogError):
        writer.plan(0, {"selected_urls": []})
    assert Path(writer.result.plan_files[0]).read_bytes() == original
    assert len(Path(writer.result.text_path).read_text(encoding="utf-8").splitlines()) == 2
    records = [json.loads(line) for line in Path(writer.result.jsonl_path).read_text(encoding="utf-8").splitlines()]
    assert records[0]["source_url"] == raw


def test_close_correction_is_independent_of_history_failure(tmp_path):
    async def scenario():
        async with compose(tmp_path, Gallery()) as service:
            await attach_transport(service)
            result = await service.workflow(FEED, workflow_retries=0)
            result = replace(result, history_saved=False, stop_error=error_info_for(StorageError()))
            revised, status = await _revise_after_close(service.config, result, 1, service.outputs.root)
            assert status == 1 and revised.history_saved is False
            record = events(revised)[-1]
            assert record["event"] == "run_corrected"
            assert record["run_id"] == result.run_id
            assert record["exit_code"] == 1
            assert record["event_number"] == result.workflow_log.event_sequence + 1

    asyncio.run(scenario())


@pytest.mark.parametrize("timeout", [False, True])
def test_cancel_or_timeout_distinguishes_interrupted_and_not_started(tmp_path, timeout):
    started = asyncio.Event()

    class Blocked(Gallery):
        async def inspect(self, url, context):
            if url == A.url and (not timeout or self.checks > 1):
                started.set()
                await asyncio.Event().wait()
            if timeout and url == A.url:
                raise PluginError()
            return await super().inspect(url, context)

    async def scenario():
        async with compose(tmp_path, Blocked()) as service:
            await attach_transport(service)
            task = asyncio.create_task(
                service.workflow(FEED, workflow_retry_delay=0, workflow_retry_timeout=0.2 if timeout else None)
            )
            await asyncio.wait_for(started.wait(), 5)
            if not timeout:
                task.cancel()
            with pytest.raises(WorkflowRetryTimeoutError if timeout else asyncio.CancelledError) as captured:
                await task
            result = captured.value.workflow_result
            records = events(result)
            assert any(r["event"] == "url_interrupted" and r["url"] == A.url for r in records)
            assert records[-1]["timed_out"] is timeout
            assert records[-1]["cancelled"] is not timeout
            if not timeout:
                assert not any(r["event"] == "url_started" and r["url"] == B.url for r in records)
                assert records[-1]["items"][1]["attempt_count"] == 0

    asyncio.run(scenario())


def test_cancel_waits_for_started_plan_write(tmp_path, monkeypatch):
    started, release = threading.Event(), threading.Event()
    original = FileSystem.write_bytes_atomic_new

    def block(self, relative, data):
        if str(relative).endswith(".plan.json"):
            started.set()
            assert release.wait(5)
        return original(self, relative, data)

    monkeypatch.setattr(FileSystem, "write_bytes_atomic_new", block)

    async def scenario():
        gallery = Gallery()
        async with compose(tmp_path, gallery) as service:
            task = asyncio.create_task(service.workflow(FEED))
            assert await asyncio.to_thread(started.wait, 5)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError) as captured:
                await task
            result = captured.value.workflow_result
            assert len(result.workflow_log.plan_files) == 1
            assert gallery.seen == []
            assert events(result)[-1]["cancelled"]

    asyncio.run(scenario())


def test_parallel_runs_have_separate_files_and_history_ids(tmp_path):
    async def scenario():
        async with compose(tmp_path, Gallery(), existing_file="rename") as first:
            async with compose(tmp_path, Gallery(), existing_file="rename") as second:
                await attach_transport(first)
                await attach_transport(second)
                one, two = await asyncio.gather(
                    first.workflow(FEED, workflow_retries=0), second.workflow(FEED + "2", workflow_retries=0)
                )
                assert one.run_id != two.run_id
                assert set(one.workflow_log.plan_files).isdisjoint(two.workflow_log.plan_files)
                assert plans(one)[0]["source_url"] == FEED
                assert plans(two)[0]["source_url"] == FEED + "2"

    asyncio.run(scenario())


def test_cancellation_during_final_log_write_preserves_success_and_revises_history(tmp_path, monkeypatch):
    started, release = threading.Event(), threading.Event()
    original = WorkflowLogWriter.finish

    def block(self, result, exit_code, outcome, *, correction=False):
        if not correction:
            started.set()
            assert release.wait(5)
        return original(self, result, exit_code, outcome, correction=correction)

    monkeypatch.setattr(WorkflowLogWriter, "finish", block)

    async def scenario():
        from image_downloader import WorkflowStateService

        async with compose(tmp_path, Gallery()) as service:
            await attach_transport(service)
            task = asyncio.create_task(service.workflow(FEED, workflow_retries=0))
            assert await asyncio.to_thread(started.wait, 5)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError) as captured:
                await task
            result = captured.value.workflow_result
            assert result.cancelled
            assert all(item.status == "success" for item in result.items)
            assert events(result)[-1]["event"] == "run_corrected"
            assert events(result)[-1]["exit_code"] == 130
            saved = WorkflowStateService(service.config).get_run(result.run_id)
            assert saved.exit_code == 130

    asyncio.run(scenario())


def test_final_text_failure_is_recorded_in_surviving_jsonl(tmp_path, monkeypatch):
    original = FileSystem.open_text_append
    fail_final_text = False
    finish = WorkflowLogWriter.finish

    def finishing(self, *args, **kwargs):
        nonlocal fail_final_text
        fail_final_text = True
        return finish(self, *args, **kwargs)

    def fail(self, relative, *args, **kwargs):
        if fail_final_text and self.root.name == "workflow" and str(relative).endswith(".log"):
            raise OSError("private")
        return original(self, relative, *args, **kwargs)

    monkeypatch.setattr(WorkflowLogWriter, "finish", finishing)
    monkeypatch.setattr(FileSystem, "open_text_append", fail)

    async def scenario():
        async with compose(tmp_path, Gallery()) as service:
            await attach_transport(service)
            result = await service.workflow(FEED, workflow_retries=0)
            assert result.workflow_log.text_saved is False
            record = events(result)[-1]
            assert record["event"] == "recording_failed"
            assert record["text_saved"] is False
            assert record["warnings"]

    asyncio.run(scenario())
