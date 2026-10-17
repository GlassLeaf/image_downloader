"""Typed read boundaries preserve state bytes, lock order and source identities."""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from hashlib import sha256

import pytest

from image_downloader import AppConfig, StorageSafetyError, UpdateCandidate, UpdateSnapshot, UpdateStateError
from image_downloader.application.workflow_state import WorkflowStateService
from image_downloader.storage import FileSystem
from image_downloader.storage.source_identity import source_key
from image_downloader.storage.state import UpdateState
from image_downloader.storage.workflow import WorkflowState


@pytest.mark.parametrize(
    "url", ["https://example.test/feed?q=日本語", "https://example.test:443/feed/", "opaque:source"]
)
def test_source_keys_keep_the_existing_utf8_digest(url):
    assert source_key(url) == sha256(url.encode("utf-8")).hexdigest()


def test_read_feeds_detaches_immutable_values_without_rewriting(tmp_path):
    filesystem = FileSystem(tmp_path)
    state = WorkflowState(filesystem)
    url = "https://example.test/feed"
    snapshot = UpdateSnapshot(url, (UpdateCandidate(url + "/a", "a", "1"),), datetime(2026, 10, 1, tzinfo=UTC))
    state.prepare("plugin", url, snapshot, "updated")
    before = (tmp_path / "workflow.json").read_bytes()
    with state.read_feeds() as feeds:
        feed = feeds[0]
        assert feed.plugin_id == "plugin" and feed.feed_key == source_key(url)
        assert feed.checked_at == snapshot.checked_at
        assert feed.items[0].candidate == snapshot.candidates[0]
        assert not feed.items[0].completed
        with pytest.raises(FrozenInstanceError):
            feed.plugin_id = "changed"
        with pytest.raises(FrozenInstanceError):
            feed.items[0].completed = True
    assert (tmp_path / "workflow.json").read_bytes() == before
    state.complete("plugin", url, url + "/a")
    with state.read_feeds() as current:
        assert current[0].items[0].completed
    assert not feed.items[0].completed


def test_read_feeds_keeps_lock_through_consumer_and_releases_after_error(tmp_path, monkeypatch):
    state = WorkflowState(FileSystem(tmp_path))
    events = []

    @contextmanager
    def lock(relative):
        events.append(("acquire", str(relative)))
        try:
            yield
        finally:
            events.append(("release", str(relative)))

    monkeypatch.setattr(state, "lock", lock)
    with pytest.raises(RuntimeError):
        with state.read_feeds() as feeds:
            assert feeds == ()
            assert events == [("acquire", "workflow.lock")]
            events.append(("history", "read"))
            raise RuntimeError("consumer failed")
    assert events[-1] == ("release", "workflow.lock")


def test_state_service_reads_history_inside_the_short_workflow_lock(tmp_path, monkeypatch):
    config = AppConfig.model_validate({"storage": {"data_root": str(tmp_path)}})
    service = WorkflowStateService(config)
    held = False
    events = []

    @contextmanager
    def lock(relative):
        nonlocal held
        assert str(relative) == "workflow.lock"
        held = True
        events.append("workflow")
        try:
            yield
        finally:
            held = False

    def history():
        assert held
        events.append("history")
        return ()

    def forbidden_feed_lock(*_args):
        pytest.fail("readers must not acquire the long-lived feed lock")

    monkeypatch.setattr(service._state, "lock", lock)
    monkeypatch.setattr(service._state, "feed_lock", forbidden_feed_lock)
    monkeypatch.setattr(service._history, "visible", history)
    assert service.list_workflows() == ()
    assert events == ["workflow", "history"]
    assert not held


@pytest.mark.parametrize("timestamp", ["not a date", "2026-10-01T00:00:00", "999999999999-10-01"])
def test_read_feeds_preserves_timestamp_diagnostics_and_plugin_filter(tmp_path, timestamp):
    filesystem = FileSystem(tmp_path)
    content = json.dumps(
        {"schema_version": 1, "sources": {"bad": {"key": {"checked_at": timestamp, "records": {}}}}}
    )
    filesystem.write_bytes_atomic("workflow.json", content.encode())
    state = WorkflowState(filesystem)
    with state.read_feeds(plugin_id="other") as feeds:
        assert not feeds
    with pytest.raises(UpdateStateError, match="invalid timestamp"):
        with state.read_feeds():
            pytest.fail("an invalid timestamp must not escape into application code")
    assert filesystem.read_text("workflow.json") == content


@pytest.mark.parametrize("content", [b'{"records": {}}', b'{"schema_version": 2, "sources": {}, "legacy_records": {}}'])
def test_update_validation_does_not_migrate_or_write(tmp_path, content):
    filesystem = FileSystem(tmp_path)
    filesystem.write_bytes_atomic("updates.json", content)
    UpdateState(filesystem).validate()
    assert (tmp_path / "updates.json").read_bytes() == content


def test_update_validation_preserves_corrupt_state(tmp_path):
    filesystem = FileSystem(tmp_path)
    filesystem.write_bytes_atomic("updates.json", b"broken")
    with pytest.raises(UpdateStateError):
        UpdateState(filesystem).validate()
    assert (tmp_path / "updates.json").read_bytes() == b"broken"


def test_shorten_component_uses_the_requested_parent_and_validates_it(tmp_path, monkeypatch):
    from image_downloader.storage import filesystem as module
    from image_downloader.storage._path_limits import _ComponentLimit

    parents = []

    def limit(parent):
        parents.append(parent)
        return _ComponentLimit(32, False)

    monkeypatch.setattr(module, "_component_limit", limit)
    filesystem = FileSystem(tmp_path)
    assert len(filesystem.shorten_component("a" * 80, parent="host")) == 32
    assert parents == [tmp_path / "host"]
    with pytest.raises(StorageSafetyError):
        filesystem.shorten_component("a", parent="../outside")
