from __future__ import annotations

import asyncio
import json
import multiprocessing
from dataclasses import replace

import pytest
from support.workflow import FEED, A, B, Gallery, compose, snapshot

from image_downloader import (
    AppConfig,
    InterProcessLockError,
    UpdateCandidate,
    UpdateStateError,
    WorkflowPlanRequest,
    WorkflowStateConflictError,
    WorkflowStateFormatError,
    WorkflowStateService,
    WorkflowTargetSelector,
)
from image_downloader.configuration.paths import resolve_paths
from image_downloader.storage.filesystem import FileSystem
from image_downloader.storage.workflow import WorkflowState


def setup_state(tmp_path):
    config = AppConfig.model_validate({"storage": {"data_root": str(tmp_path / "data")}})
    state = WorkflowState(FileSystem(resolve_paths(config)["state"]))
    return state, WorkflowStateService(config)


def test_exact_url_updates_all_registrations_and_preserves_other_urls(tmp_path):
    state, service = setup_state(tmp_path)
    duplicate = UpdateCandidate(A.url, "also-a", "1")
    for plugin, feed in (("p", FEED), ("q", FEED), ("p", FEED + "2")):
        state.prepare(plugin, feed, snapshot(A, duplicate, B), "updated")
    selector = WorkflowTargetSelector(url=A.url)
    plan = service.plan_changes(selector, "completed")
    assert len(plan.changes) == 6
    result = service.apply_changes(plan, [A.url])
    assert result.matched_urls == result.approved_urls == 1
    assert result.changed_records == 6
    assert all(row.completed for row in service.list_targets(selector))
    assert all(row.status == "pending" for row in service.list_targets(WorkflowTargetSelector(url=B.url)))
    assert not any(change.requires_write for change in service.plan_changes(selector, "completed").changes)
    raw = json.loads(state.filesystem.read_text("workflow.json"))
    assert raw["schema_version"] == 1
    assert all(
        "completed" not in row
        for feeds in raw["sources"].values()
        for feed in feeds.values()
        for row in feed["records"].values()
    )


def test_regex_status_source_and_plugin_filters_apply_together(tmp_path):
    state, service = setup_state(tmp_path)
    secret = UpdateCandidate(A.url + "?token=private", "s", "1")
    for plugin, feed in (("p", FEED), ("q", FEED), ("p", FEED + "2")):
        state.prepare(plugin, feed, snapshot(A, secret, B), "updated")
    state.complete("p", FEED, A.url)
    selector = WorkflowTargetSelector(
        url_regex=r"token=private$", source_url=FEED, plugin_id="p", from_status="pending"
    )
    plan = service.plan_changes(selector, "cancelled")
    assert len(plan.changes) == 1
    service.apply_changes(plan, [secret.url])
    assert service.list_targets(replace(selector, from_status="cancelled"))[0].status == "cancelled"
    assert not service.list_targets(replace(selector, url_regex="PRIVATE$"))


@pytest.mark.parametrize("mutation", ["status", "revision", "new_match"])
def test_confirmation_detects_changed_matching_state_and_writes_nothing(tmp_path, mutation):
    state, service = setup_state(tmp_path)
    state.prepare("p", FEED, snapshot(A, B), "updated")
    selector = WorkflowTargetSelector(all_targets=True)
    plan = service.plan_changes(selector, "completed")
    if mutation == "status":
        state.set_status("p", FEED, A.url, "cancelled")
    elif mutation == "revision":
        state.prepare("p", FEED, snapshot(replace(A, revision="2"), B), "updated")
    else:
        state.prepare("q", FEED, snapshot(A), "updated")
    before = state.filesystem.read_text("workflow.json")
    with pytest.raises(WorkflowStateConflictError):
        service.apply_changes(plan, [A.url, B.url])
    assert state.filesystem.read_text("workflow.json") == before


def test_unrelated_feed_changes_are_preserved_and_do_not_conflict(tmp_path):
    state, service = setup_state(tmp_path)
    state.prepare("p", FEED, snapshot(A), "updated")
    plan = service.plan_changes(WorkflowTargetSelector(source_url=FEED, all_targets=True), "completed")
    state.prepare("q", FEED + "other", snapshot(B), "updated")
    service.apply_changes(plan, [A.url])
    assert service.list_targets(WorkflowTargetSelector(url=B.url))[0].status == "pending"


def test_reset_cancelled_and_changed_revision_are_selected_again(tmp_path):
    state, service = setup_state(tmp_path)
    state.prepare("p", FEED, snapshot(A, B), "updated")
    selector = WorkflowTargetSelector(all_targets=True)
    service.apply_changes(service.plan_changes(selector, "completed"), [A.url, B.url])
    assert not state.preview("p", FEED, snapshot(A, B), "updated")[2]
    service.apply_changes(service.plan_changes(WorkflowTargetSelector(url=A.url), "cancelled"), [A.url])
    assert tuple(state.preview("p", FEED, snapshot(A, B), "updated")[2]) == (A.url,)
    service.apply_changes(service.plan_changes(selector, "pending"), [A.url, B.url])
    assert tuple(state.preview("p", FEED, snapshot(A, B), "updated")[2]) == (A.url, B.url)
    state.complete("p", FEED, A.url)
    assert A.url in state.preview("p", FEED, snapshot(replace(A, revision="2"), B), "updated")[2]
    assert len(state.preview("p", FEED, snapshot(A, B), "all")[2]) == 2


@pytest.mark.parametrize(
    "record",
    [
        {"url": A.url, "content_id": "a", "revision": "1", "completed": True},
        {"url": A.url, "content_id": "a", "revision": "1", "status": "unknown"},
        {"url": A.url, "content_id": "a", "revision": "1", "status": []},
    ],
)
def test_obsolete_or_invalid_records_are_rejected_without_rewriting(tmp_path, record):
    state, service = setup_state(tmp_path)
    payload = json.dumps(
        {
            "schema_version": 1,
            "sources": {"p": {"key": {"checked_at": snapshot().checked_at.isoformat(), "records": {"id:a": record}}}},
        }
    )
    state.filesystem.write_bytes_atomic("workflow.json", payload.encode())
    expected = WorkflowStateFormatError if "completed" in record else UpdateStateError
    with pytest.raises(expected):
        service.list_targets(WorkflowTargetSelector(all_targets=True))
    assert state.filesystem.read_text("workflow.json") == payload


def test_refresh_only_upserts_approved_candidates_and_preserves_rejected_and_removed(tmp_path):
    async def scenario():
        gallery = Gallery()
        async with compose(tmp_path, gallery) as runtime:
            state = WorkflowState(runtime.state.filesystem)
            removed = UpdateCandidate("https://example.test/old", "old", "1")
            state.prepare("core.generic-html", FEED, snapshot(removed), "updated")
            state.complete("core.generic-html", FEED, removed.url)
            before = state.filesystem.read_text("workflow.json")
            selector = WorkflowTargetSelector(source_url=FEED, all_targets=True)
            plan = await runtime.plan_workflow_targets(WorkflowPlanRequest(url=FEED), selector, "completed")
            assert state.filesystem.read_text("workflow.json") == before
            assert not gallery.seen  # No manifest/image download.
            assert not runtime.state.filesystem.exists("updates.json")
            service = WorkflowStateService(runtime.config)
            service.apply_changes(plan, [A.url])
            rows = service.list_targets(WorkflowTargetSelector(all_targets=True))
            assert {row.candidate.url for row in rows} == {A.url, removed.url}
            assert all(row.status == "completed" for row in rows)
            assert tuple(state.preview("core.generic-html", FEED, snapshot(A, B), "updated")[2]) == (B.url,)
            assert not service.list_runs()

    asyncio.run(scenario())


def test_refresh_all_lists_current_status_and_can_reset_completed_without_touching_rejected_revision(tmp_path):
    async def scenario():
        gallery = Gallery()
        async with compose(tmp_path, gallery) as runtime:
            state = WorkflowState(runtime.state.filesystem)
            state.prepare("core.generic-html", FEED, snapshot(A, B), "updated")
            state.complete("core.generic-html", FEED, A.url)
            gallery.candidates = (A, replace(B, revision="2"))
            selector = WorkflowTargetSelector(source_url=FEED, all_targets=True)
            request = WorkflowPlanRequest(url=FEED, download_scope="all")
            listed = await runtime.plan_workflow_targets(request, selector)
            assert [c.after.status for c in listed.changes] == ["completed", "pending"]
            plan = await runtime.plan_workflow_targets(request, selector, "pending")
            service = WorkflowStateService(runtime.config)
            service.apply_changes(plan, [A.url])
            rows = service.list_targets(selector)
            assert all(row.status == "pending" for row in rows)
            assert next(row for row in rows if row.candidate.url == B.url).candidate.revision == "1"

    asyncio.run(scenario())


def _hold_feed_lock(root, ready, release):
    state = WorkflowState(FileSystem(root))
    with state.feed_lock("q", FEED):
        ready.set()
        assert release.wait(30)


def test_other_process_workflow_lock_blocks_apply_without_partial_writes(tmp_path):
    state, service = setup_state(tmp_path)
    state.prepare("p", FEED, snapshot(A), "updated")
    state.prepare("q", FEED, snapshot(A), "updated")
    plan = service.plan_changes(WorkflowTargetSelector(all_targets=True), "completed")
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    process = context.Process(target=_hold_feed_lock, args=(state.filesystem.root, ready, release))
    process.start()
    try:
        assert ready.wait(20)
        service._state.lock_timeout_seconds = 0.15
        before = state.filesystem.read_text("workflow.json")
        with pytest.raises(InterProcessLockError):
            service.apply_changes(plan, [A.url])
        assert state.filesystem.read_text("workflow.json") == before
    finally:
        release.set()
        process.join(20)
        if process.is_alive():
            process.terminate()
            process.join(10)
    assert process.exitcode == 0
