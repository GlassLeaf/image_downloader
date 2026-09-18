from __future__ import annotations

import asyncio
import json
import multiprocessing
import threading
import time
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest

from image_downloader.config import AppConfig
from image_downloader.exceptions import DownloaderError, InterProcessLockError, PluginError
from image_downloader.models import UpdateCandidate, UpdateChangeKind, UpdateSnapshot
from image_downloader.runtime import RuntimeComposer
from image_downloader.storage import FileSystem
from image_downloader.storage.interprocess_lock import InterProcessFileLock
from image_downloader.storage.state import UpdateState

PLUGIN_ID = "core.generic-html"
FEED_A = "https://example.test/feed/a"
FEED_B = "https://example.test/feed/b"


def _snapshot(source_url: str, *candidates: UpdateCandidate) -> UpdateSnapshot:
    return UpdateSnapshot(source_url, candidates, datetime.now(UTC))


def _state(tmp_path: Path) -> UpdateState:
    return UpdateState(FileSystem(tmp_path.resolve()))


def _source_key(url: str) -> str:
    return sha256(url.encode("utf-8")).hexdigest()


def _apply_in_process(root: str, feed: str, revision: str, ready: object, start: object, result: object) -> None:
    state = UpdateState(FileSystem(Path(root)))
    original_write = state._write

    def slow_write(document: object) -> None:
        time.sleep(0.15)
        original_write(document)  # type: ignore[arg-type]

    state._write = slow_write  # type: ignore[method-assign]
    ready.set()  # type: ignore[attr-defined]
    start.wait()  # type: ignore[attr-defined]
    try:
        changes = state.apply_snapshot(
            PLUGIN_ID,
            feed,
            _snapshot(feed, UpdateCandidate("https://example.test/item", "shared", revision)),
        )
        result.put((revision, [change.kind.value for change in changes]))  # type: ignore[attr-defined]
    except Exception as exc:
        result.put(repr(exc))  # type: ignore[attr-defined]


def _hold_update_lock(path: str, ready: object) -> None:
    with InterProcessFileLock(Path(path)):
        ready.set()  # type: ignore[attr-defined]
        time.sleep(10)


def test_update_history_is_isolated_by_plugin_and_requested_feed(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = AppConfig.model_validate(
            {
                "storage": {"data_root": str((tmp_path / "data").resolve())},
                "plugins": {"root": str((tmp_path / "plugins").resolve())},
                "security": {"plugin_verification": "off"},
                "logging": {"console": {"enabled": False}},
                "notification": {"enabled": False},
            }
        )
        service = RuntimeComposer(
            config,
            config_root=tmp_path.resolve(),
            plugin_root=(tmp_path / "plugins").resolve(),
        ).compose()
        record = service.registry.records[PLUGIN_ID]
        snapshots = {
            FEED_A: [
                _snapshot(FEED_A, UpdateCandidate("https://example.test/a1", "shared", "1")),
                _snapshot(FEED_A, UpdateCandidate("https://example.test/a1", "shared", "1")),
                _snapshot(FEED_A),
            ],
            FEED_B: [
                _snapshot(FEED_B, UpdateCandidate("https://example.test/b1", "shared", "1")),
                _snapshot(FEED_B, UpdateCandidate("https://example.test/b1", "shared", "1")),
            ],
        }

        class UpdatePlugin:
            def auth_flow(self, _context: object) -> None:
                return None

            async def check_updates(self, url: str, _context: object) -> UpdateSnapshot:
                return snapshots[url].pop(0)

        plugin = UpdatePlugin()
        service.registry.resolve = lambda *_args, **_kwargs: (record, plugin)  # type: ignore[method-assign]
        try:
            first_a = await service.check_updates(FEED_A)
            first_b = await service.check_updates(FEED_B)
            second_a = await service.check_updates(FEED_A)
            removed_a = await service.check_updates(FEED_A)
            second_b = await service.check_updates(FEED_B)

            assert [change.kind for change in first_a.changes] == [UpdateChangeKind.ADDED]
            assert [change.kind for change in first_b.changes] == [UpdateChangeKind.ADDED]
            assert second_a.changes == ()
            assert [change.kind for change in removed_a.changes] == [UpdateChangeKind.REMOVED]
            assert removed_a.changes[0].url == "https://example.test/a1"
            assert second_b.changes == ()

            persisted = json.loads(service.state.filesystem.read_text(Path("updates.json")))
            assert persisted["schema_version"] == 2
            assert set(persisted["sources"][PLUGIN_ID]) == {_source_key(FEED_A), _source_key(FEED_B)}
            assert persisted["sources"][PLUGIN_ID][_source_key(FEED_A)]["records"] == {}
            assert set(persisted["sources"][PLUGIN_ID][_source_key(FEED_B)]["records"]) == {"id:shared"}
            assert FEED_A not in json.dumps(persisted)
        finally:
            await service.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("explicit_version", (False, True))
def test_v1_migration_preserves_unscoped_history_without_false_removals(
    tmp_path: Path,
    explicit_version: bool,
) -> None:
    state = _state(tmp_path)
    legacy = {
        f"{PLUGIN_ID}:shared": {
            "url": "https://example.test/a1",
            "revision": "1",
            "content_id": "shared",
        },
        f"{PLUGIN_ID}:unknown-feed": {
            "url": "https://example.test/unknown",
            "revision": "1",
            "content_id": "unknown-feed",
        },
    }
    old_payload: dict[str, object] = {"checked_at": datetime.now(UTC).isoformat(), "records": legacy}
    if explicit_version:
        old_payload["schema_version"] = 1
    state.filesystem.write_bytes_atomic(state.relative, json.dumps(old_payload).encode())

    first = state.apply_snapshot(
        PLUGIN_ID,
        FEED_A,
        _snapshot(FEED_A, UpdateCandidate("https://example.test/a1", "shared", "1")),
    )
    assert first == ()
    persisted = json.loads(state.filesystem.read_text(state.relative))
    assert persisted["schema_version"] == 2
    assert persisted["legacy_records"] == legacy
    assert persisted["sources"][PLUGIN_ID][_source_key(FEED_A)]["records"]["id:shared"] == legacy[f"{PLUGIN_ID}:shared"]

    second = state.apply_snapshot(PLUGIN_ID, FEED_A, _snapshot(FEED_A))
    assert [(change.kind, change.url) for change in second] == [(UpdateChangeKind.REMOVED, "https://example.test/a1")]
    assert state.records() == legacy


def test_legacy_record_only_matches_exact_url_and_content_id(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state.save(
        {
            f"{PLUGIN_ID}:same-id": {
                "url": "https://example.test/old",
                "revision": "1",
                "content_id": "same-id",
            }
        }
    )

    changes = state.apply_snapshot(
        PLUGIN_ID,
        FEED_B,
        _snapshot(FEED_B, UpdateCandidate("https://example.test/new", "same-id", "1")),
    )

    assert [change.kind for change in changes] == [UpdateChangeKind.ADDED]
    assert state.records()[f"{PLUGIN_ID}:same-id"]["url"] == "https://example.test/old"


def test_legacy_save_preserves_already_committed_feed_snapshot(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state.apply_snapshot(PLUGIN_ID, FEED_A, _snapshot(FEED_A, UpdateCandidate("https://example.test/a", "a")))
    state.save({"legacy": {"url": "https://example.test/old", "revision": "1", "content_id": "old"}})

    document = json.loads(state.filesystem.read_text(state.relative))
    assert document["legacy_records"]["legacy"]["content_id"] == "old"
    assert "id:a" in document["sources"][PLUGIN_ID][_source_key(FEED_A)]["records"]


def test_plugin_ids_are_separate_even_for_the_same_feed(tmp_path: Path) -> None:
    state = _state(tmp_path)
    candidate = UpdateCandidate("https://example.test/item", "same-id", "1")
    snapshot = _snapshot(FEED_A, candidate)

    assert [change.kind for change in state.apply_snapshot("com.example.first", FEED_A, snapshot)] == [
        UpdateChangeKind.ADDED
    ]
    assert [change.kind for change in state.apply_snapshot("com.example.second", FEED_A, snapshot)] == [
        UpdateChangeKind.ADDED
    ]
    assert state.apply_snapshot("com.example.first", FEED_A, snapshot) == ()


def test_duplicate_identity_does_not_overwrite_scoped_state(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state.apply_snapshot(PLUGIN_ID, FEED_A, _snapshot(FEED_A, UpdateCandidate("https://example.test/a", "same")))
    before = state.filesystem.read_text(state.relative)

    with pytest.raises(PluginError, match="duplicate candidate identities"):
        state.apply_snapshot(
            PLUGIN_ID,
            FEED_A,
            _snapshot(
                FEED_A,
                UpdateCandidate("https://example.test/a", "same"),
                UpdateCandidate("https://example.test/b", "same"),
            ),
        )

    assert state.filesystem.read_text(state.relative) == before


def test_future_schema_version_is_rejected_without_overwrite(tmp_path: Path) -> None:
    state = _state(tmp_path)
    future = '{"schema_version": 3, "sources": {}}'
    state.filesystem.write_bytes_atomic(state.relative, future.encode())

    with pytest.raises(DownloaderError, match="schema version is unsupported"):
        state.apply_snapshot(PLUGIN_ID, FEED_A, _snapshot(FEED_A))

    assert state.filesystem.read_text(state.relative) == future


@pytest.mark.parametrize("same_feed", (False, True))
def test_parallel_processes_commit_feed_updates_without_lost_history(tmp_path: Path, same_feed: bool) -> None:
    state = _state(tmp_path)
    legacy = {"legacy": {"url": "https://example.test/legacy", "revision": "0", "content_id": "legacy"}}
    state.filesystem.write_bytes_atomic(state.relative, json.dumps({"records": legacy}).encode())
    context = multiprocessing.get_context("spawn")
    ready = [context.Event(), context.Event()]
    start = context.Event()
    results = context.Queue()
    feeds = (FEED_A, FEED_A if same_feed else FEED_B)
    workers = [
        context.Process(
            target=_apply_in_process,
            args=(str(tmp_path.resolve()), feed, str(index + 1), ready[index], start, results),
        )
        for index, feed in enumerate(feeds)
    ]
    for worker in workers:
        worker.start()
    try:
        assert all(event.wait(10) for event in ready)
        start.set()
        outcomes = [results.get(timeout=10) for _ in workers]
        for worker in workers:
            worker.join(timeout=10)
            assert worker.exitcode == 0
    finally:
        start.set()
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
            worker.join(timeout=5)

    assert all(isinstance(result, tuple) for result in outcomes)
    document = json.loads(state.filesystem.read_text(state.relative))
    assert document["schema_version"] == 2
    assert document["legacy_records"] == legacy
    assert state.filesystem.exists(state.lock_relative)
    if same_feed:
        assert sorted(result[1] for result in outcomes) == [["added"], ["changed"]]
        changed_revision = next(result[0] for result in outcomes if result[1] == ["changed"])
        saved_record = document["sources"][PLUGIN_ID][_source_key(FEED_A)]["records"]["id:shared"]
        assert saved_record["revision"] == changed_revision
    else:
        assert all(result[1] == ["added"] for result in outcomes)
        assert set(document["sources"][PLUGIN_ID]) == {_source_key(FEED_A), _source_key(FEED_B)}


def test_update_lock_timeout_is_operation_error_and_crash_releases_lock(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state.apply_snapshot(PLUGIN_ID, FEED_A, _snapshot(FEED_A))
    before = state.filesystem.read_text(state.relative)
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    holder = context.Process(target=_hold_update_lock, args=(str(state.filesystem.path(state.lock_relative)), ready))
    holder.start()
    try:
        assert ready.wait(10)
        contender = UpdateState(state.filesystem, lock_timeout_seconds=0)
        with pytest.raises(InterProcessLockError, match="timed out"):
            contender.apply_snapshot(PLUGIN_ID, FEED_B, _snapshot(FEED_B))
        with pytest.raises(InterProcessLockError, match="timed out"):
            contender.save({})

        async def check_loop() -> None:
            waiting = UpdateState(state.filesystem, lock_timeout_seconds=0.3)
            task = asyncio.create_task(waiting.apply_snapshot_async(PLUGIN_ID, FEED_B, _snapshot(FEED_B)))
            await asyncio.sleep(0.05)
            assert not task.done()  # Lock wait must not block the event loop.
            with pytest.raises(InterProcessLockError, match="timed out"):
                await task

        asyncio.run(check_loop())
        assert state.filesystem.read_text(state.relative) == before
    finally:
        holder.terminate()  # Simulate abrupt process death while holding the OS lock.
        holder.join(timeout=10)
        assert not holder.is_alive()
    state.apply_snapshot(PLUGIN_ID, FEED_B, _snapshot(FEED_B))
    assert set(json.loads(state.filesystem.read_text(state.relative))["sources"][PLUGIN_ID]) == {
        _source_key(FEED_A), _source_key(FEED_B)
    }


def test_cancelled_update_waits_for_started_save_to_finish(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = _state(tmp_path)
    started = threading.Event()
    release = threading.Event()
    original_write = state._write

    def paused_write(document: object) -> None:
        started.set()
        assert release.wait(5)
        original_write(document)  # type: ignore[arg-type]

    monkeypatch.setattr(state, "_write", paused_write)

    async def scenario() -> None:
        task = asyncio.create_task(state.apply_snapshot_async(PLUGIN_ID, FEED_A, _snapshot(FEED_A)))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            task.cancel()
            await asyncio.sleep(0.05)
            assert not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    document = json.loads(state.filesystem.read_text(state.relative))
    assert _source_key(FEED_A) in document["sources"][PLUGIN_ID]
