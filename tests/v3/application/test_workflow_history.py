from __future__ import annotations

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from test_workflow import FEED, B, Gallery, attach_transport, compose

from image_downloader import (
    PluginError,
    UpdateStateError,
    WorkflowResult,
    WorkflowRetryTimeoutError,
    WorkflowRunRecord,
    WorkflowStateService,
)
from image_downloader.application.workflow_reporting import history_details
from image_downloader.configuration.models import WorkflowHistory
from image_downloader.storage.filesystem import FileSystem
from image_downloader.storage.workflow_history import WorkflowHistoryStore, encode_records

NOW = datetime(2026, 10, 2, tzinfo=UTC)


def record(*, ended=NOW, source=FEED, plugin="test.gallery", padding=""):
    result = WorkflowResult(source, "updated", None, (), ())
    details = history_details(result, Path.cwd())
    return WorkflowRunRecord(str(uuid4()), plugin, "a" * 64, source + padding, ended, ended, 0, details)


def store(tmp_path, *, days=90, size=104857600):
    return WorkflowHistoryStore(FileSystem(tmp_path.resolve()), WorkflowHistory(max_age_days=days, max_size_bytes=size))


def test_retention_boundary_and_read_without_rewrite(tmp_path):
    history = store(tmp_path)
    boundary = record(ended=NOW - timedelta(days=90))
    recent = record(ended=NOW - timedelta(days=90) + timedelta(microseconds=1))
    history.save(boundary, now=boundary.ended_at)
    history.save(recent, now=recent.ended_at)
    before = (tmp_path / "workflow-history.json").read_bytes()
    assert history.visible(NOW) == [recent]
    assert (tmp_path / "workflow-history.json").read_bytes() == before
    preview = history.prune(dry_run=True, now=NOW)
    assert preview.deleted_run_ids == (boundary.run_id,)
    assert preview.expired_count == 1
    assert (tmp_path / "workflow-history.json").read_bytes() == before
    assert history.prune(now=NOW).deleted_run_ids == preview.deleted_run_ids


def test_utf8_size_oldest_eviction_and_oversized_new_result(tmp_path):
    old, middle, large = (
        record(ended=NOW - timedelta(seconds=2)),
        record(ended=NOW - timedelta(seconds=1)),
        record(padding="日本語" * 300),
    )
    cap = len(encode_records([old, middle]))
    history = store(tmp_path, size=cap)
    history.save(old, now=NOW)
    history.save(middle, now=NOW)
    assert history.save(large, now=NOW)  # Single incoming result exceeds cap: keep all.
    assert len(history.visible(NOW)) == 3
    preview = history.prune(dry_run=True, now=NOW)
    assert preview.deleted_run_ids == (old.run_id, middle.run_id)
    assert preview.over_limit
    assert preview.before_bytes == len((tmp_path / "workflow-history.json").read_bytes())
    assert preview.after_bytes == len(encode_records([large]))
    history.prune(now=NOW)
    assert history.visible(NOW) == [large]
    assert history.prune(now=NOW + timedelta(days=91)).expired_count == 1
    assert history.visible(NOW + timedelta(days=91)) == []


def test_normal_capacity_prunes_oldest_whole_run(tmp_path):
    runs = [record(ended=NOW + timedelta(seconds=i)) for i in range(3)]
    history = store(tmp_path, size=len(encode_records(runs[1:])))
    for run in runs:
        history.save(run, now=NOW)
    assert history.visible(NOW) == runs[1:]


def test_prune_measures_actual_utf8_bytes_including_crlf(tmp_path):
    history = store(tmp_path)
    payload = json.loads(encode_records([record(padding="日本語")]))
    raw = json.dumps(payload, indent=2, ensure_ascii=False).replace("\n", "\r\n").encode("utf-8")
    path = tmp_path / "workflow-history.json"
    path.write_bytes(raw)
    preview = history.prune(dry_run=True, now=NOW)
    assert preview.before_bytes == len(raw)
    assert path.read_bytes() == raw


def test_concurrent_merge_and_same_run_revision(tmp_path):
    history = store(tmp_path)
    runs = [record(plugin=f"plugin.{i}") for i in range(8)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda r: history.save(r, now=NOW), runs))
    assert {r.run_id for r in history.visible(NOW)} == {r.run_id for r in runs}
    history.revise(runs[0].run_id, dict(runs[0].details), 5)
    assert len(history.visible(NOW)) == 8
    assert next(r for r in history.visible(NOW) if r.run_id == runs[0].run_id).exit_code == 5


def _process_save(root, ready, start, name):
    history = store(Path(root))
    ready.put(name)
    if not start.wait(10):
        raise RuntimeError("start signal missing")
    history.save(record(plugin=name), now=NOW)


def test_independent_processes_merge_under_shared_history_lock(tmp_path):
    import multiprocessing

    context = multiprocessing.get_context("spawn")
    ready, start = context.Queue(), context.Event()
    processes = [
        context.Process(target=_process_save, args=(str(tmp_path), ready, start, f"plugin.{i}")) for i in range(3)
    ]
    try:
        for process in processes:
            process.start()
        assert {ready.get(timeout=10) for _ in processes} == {f"plugin.{i}" for i in range(3)}
        start.set()
        for process in processes:
            # Allow the 30-second product lock deadline and coverage shutdown
            # to settle before judging the worker's exit status on Windows.
            process.join(60)
            assert process.exitcode == 0
        assert {r.plugin_id for r in store(tmp_path).visible(NOW)} == {f"plugin.{i}" for i in range(3)}
    finally:
        start.set()
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(5)
        ready.close()


@pytest.mark.parametrize("damage", ["schema", "nested", "duplicate", "json"])
def test_invalid_history_never_overwritten(tmp_path, damage):
    history = store(tmp_path)
    run = record()
    value = json.loads(encode_records([run]))
    if damage == "schema":
        value["schema_version"] = 2
    elif damage == "nested":
        value["runs"][0]["details"]["summary"]["failed"] = "secret"
    elif damage == "duplicate":
        value["runs"].append(value["runs"][0])
    path = tmp_path / "workflow-history.json"
    path.write_text("{" if damage == "json" else json.dumps(value), encoding="utf-8")
    before = path.read_bytes()
    for action in (history.visible, history.prune, lambda: history.save(record())):
        with pytest.raises(UpdateStateError):
            action()
        assert path.read_bytes() == before


def test_workflow_records_success_failure_recovery_and_dry_run(tmp_path):
    async def scenario():
        gallery = Gallery()
        async with compose(tmp_path, gallery) as service:
            await attach_transport(service)
            gallery.fail = {B.url}
            first = await service.workflow(FEED, workflow_retries=0)
            assert first.history_saved and first.run_id
            state = WorkflowStateService(service.config)
            run = state.get_run(first.run_id)
            assert run.exit_code == 5
            assert run.details["summary"]["failed"] == 1
            assert "images" not in json.dumps(dict(run.details), default=list)
            assert [i.completed for i in state.get_workflow(FEED)[0].items] == [True, False]
            gallery.fail.clear()
            recovered = await service.workflow(FEED, workflow_retries=0)
            assert state.get_run(recovered.run_id).exit_code == 0
            assert recovered.run_id != first.run_id
            empty = await service.workflow(FEED, workflow_retries=0)
            assert state.get_run(empty.run_id).details["items"] == ()
            count = len(state.list_runs())
            await service.plan_workflow(FEED)
            assert len(state.list_runs()) == count
            with pytest.raises(FrozenInstanceError):
                run.exit_code = 9
            with pytest.raises(TypeError):
                run.details["cancelled"] = True

    asyncio.run(scenario())


def test_recording_failure_does_not_replace_workflow_result(tmp_path, monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise OSError("password=secret")

    monkeypatch.setattr(WorkflowHistoryStore, "save", fail)

    async def scenario():
        gallery = Gallery()
        async with compose(tmp_path, gallery) as service:
            await attach_transport(service)
            result = await service.workflow(FEED, workflow_retries=0)
            assert [i.status for i in result.items] == ["success", "success"]
            assert result.history_saved is False
            assert result.history_warning
            assert all(i.completed for i in WorkflowStateService(service.config).get_workflow(FEED)[0].items)

    asyncio.run(scenario())
    assert "secret" not in capsys.readouterr().err


def test_global_failure_is_recorded_with_original_exception(tmp_path):
    class Broken(Gallery):
        async def check_updates(self, url, context):
            raise PluginError("private secret")

    async def scenario():
        async with compose(tmp_path, Broken()) as service:
            with pytest.raises(PluginError) as error:
                await service.workflow(FEED, workflow_retries=0)
            result = error.value.workflow_result
            assert result.history_saved
            run = WorkflowStateService(service.config).get_run(result.run_id)
            assert run.exit_code == 4
            assert "private secret" not in str(run.details)

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["recovery", "timeout", "cleanup", "cancel"])
def test_journal_preserves_attempts_timeout_cleanup_and_cancellation(tmp_path, operation):
    from test_workflow_retry import RetryGallery, transport

    async def scenario():
        gallery = RetryGallery()
        if operation == "cleanup":
            cleanup_failed = False

            def cleanup():
                nonlocal cleanup_failed
                if gallery.seen and not cleanup_failed:
                    cleanup_failed = True
                    raise PluginError("private cleanup")

            gallery.cleanup_after_use = cleanup
        elif operation == "cancel":

            async def inspect(url, context):
                raise asyncio.CancelledError()

            gallery.inspect = inspect
        async with compose(tmp_path, gallery, network={"max_attempts": 1}) as service:
            await transport(service, gallery)
            options = {"workflow_retry_delay": 0}
            if operation == "timeout":
                options.update(workflow_retry_delay=100, workflow_retry_timeout=0.001)
            if operation in {"cancel", "timeout"}:
                error_type = asyncio.CancelledError if operation == "cancel" else WorkflowRetryTimeoutError
                with pytest.raises(error_type) as error:
                    await service.workflow(FEED, **options)
                result = error.value.workflow_result
            else:
                result = await service.workflow(FEED, **options)
            assert result.history_saved
            run = WorkflowStateService(service.config).get_run(result.run_id)
            assert run.exit_code == {"recovery": 0, "timeout": 5, "cleanup": 0, "cancel": 130}[operation]
            if operation == "recovery":
                assert [a["status"] for a in run.details["items"][0]["attempts"]] == ["partial", "success"]
                assert len(run.details["rounds"]) == 2
            if operation == "timeout":
                assert run.details["timed_out"] and not run.details["cancelled"]
            if operation == "cancel":
                assert run.details["cancelled"] and not run.details["timed_out"]

    asyncio.run(scenario())


def test_cancel_during_history_write_finishes_and_records_cancel(tmp_path, monkeypatch):
    started, release = threading.Event(), threading.Event()
    original = WorkflowHistoryStore.save

    def delayed(self, record, **kwargs):
        started.set()
        assert release.wait(5)
        return original(self, record, **kwargs)

    monkeypatch.setattr(WorkflowHistoryStore, "save", delayed)

    async def scenario():
        async with compose(tmp_path, Gallery()) as service:
            await attach_transport(service)
            task = asyncio.create_task(service.workflow(FEED, workflow_retries=0))
            assert await asyncio.to_thread(started.wait, 5)
            task.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError) as error:
                await task
            result = error.value.workflow_result
            assert result.cancelled and result.history_saved
            run = WorkflowStateService(service.config).get_run(result.run_id)
            assert run.exit_code == 130 and run.details["cancelled"]
            assert len(run.details["items"]) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "90"])
def test_history_configuration_requires_positive_integers(value):
    with pytest.raises(ValueError):
        WorkflowHistory(max_age_days=value)
    with pytest.raises(ValueError):
        WorkflowHistory(max_size_bytes=value)


def test_profile_filtering_history_only_feed_and_config_changes(tmp_path):
    from image_downloader.configuration.paths import resolve_paths
    from image_downloader.storage.state import UpdateState

    service = compose(tmp_path, Gallery())
    state = WorkflowStateService(service.config)
    history = WorkflowHistoryStore(FileSystem(resolve_paths(service.config)["state"]), service.config.workflow_history)
    now = datetime.now(UTC)
    old = replace(record(ended=now - timedelta(days=30)), feed_key=UpdateState._source_key(FEED))
    history.save(old, now=now)
    assert state.get_workflow(FEED)[0].checked_at is None
    assert state.get_workflow(FEED)[0].source_url == FEED
    assert state.list_runs(plugin_id="missing") == ()
    short_config = service.config.model_copy(update={"workflow_history": WorkflowHistory(max_age_days=1)})
    assert WorkflowStateService(short_config).list_workflows() == ()
    other_profile = service.config.model_copy(
        update={"profile": service.config.profile.model_copy(update={"default": "other"})}
    )
    assert WorkflowStateService(other_profile).list_runs() == ()
    asyncio.run(service.close())
