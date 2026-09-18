"""Cookie persistence, imports and cross-process merging."""

from __future__ import annotations

import multiprocessing
import sys
import time
from http.cookiejar import Cookie, CookieJar
from pathlib import Path
from types import SimpleNamespace

import pytest

from image_downloader.exceptions import AuthenticationError, InterProcessLockError
from image_downloader.storage import FileSystem
from image_downloader.storage.cookies import CookieStore
from image_downloader.storage.interprocess_lock import InterProcessFileLock


def _cookie(name: str, value: str, *, expires: int | None = None, domain: str = ".example.test") -> Cookie:
    return Cookie(
        0,
        name,
        value,
        None,
        False,
        domain,
        True,
        True,
        "/",
        True,
        False,
        expires,
        False,
        None,
        None,
        {},
        False,
    )


def _keyring(monkeypatch: pytest.MonkeyPatch) -> dict[tuple[str, str], str]:
    values: dict[tuple[str, str], str] = {}
    module = SimpleNamespace(
        get_password=lambda service, account: values.get((service, account)),
        set_password=lambda service, account, value: values.__setitem__((service, account), value),
    )
    monkeypatch.setitem(sys.modules, "keyring", module)
    return values


def _store(tmp_path: Path, name: str = "cookies") -> CookieStore:
    return CookieStore(FileSystem((tmp_path / name).resolve()))


def _fixed_key(_self: CookieStore, *, create: bool) -> bytes:
    return b"t" * 32


def _merge_in_child(root: str, name: str, ready: object, start: object, result: object) -> None:
    CookieStore._key = _fixed_key  # type: ignore[method-assign]
    store = CookieStore(FileSystem(Path(root)))
    jar = store.load()
    baseline = store.snapshot(jar)
    ready.set()  # type: ignore[attr-defined]
    start.wait()  # type: ignore[attr-defined]
    jar.set_cookie(_cookie(name, name))
    try:
        store.persist_delta(baseline, jar)
        result.put(None)  # type: ignore[attr-defined]
    except Exception as exc:
        result.put(repr(exc))  # type: ignore[attr-defined]


def _hold_lock_in_child(path: str, ready: object) -> None:
    with InterProcessFileLock(Path(path)):
        ready.set()  # type: ignore[attr-defined]
        time.sleep(10)


def test_cookie_store_round_trip_export_and_corruption_repair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _keyring(monkeypatch)
    source = _store(tmp_path, "source")
    jar = CookieJar()
    jar.set_cookie(_cookie("session", "secret"))
    jar.set_cookie(_cookie("expired", "old", expires=int(time.time()) - 10))
    source.save(jar)

    loaded = list(source.load())
    assert [(item.name, item.value) for item in loaded] == [("session", "secret")]

    exported = tmp_path / "cookies.export"
    source.export_file(exported, "passphrase")
    target = _store(tmp_path, "target")
    target.filesystem.write_bytes_atomic(Path("cookies.enc"), b"damaged")
    target.import_file(exported, "passphrase")
    assert [(item.name, item.value) for item in target.load()] == [("session", "secret")]

    with pytest.raises(AuthenticationError, match="cannot be decrypted"):
        target.import_file(exported, "wrong passphrase")


def test_cookie_store_empty_jar_is_persisted_and_validated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    keys = _keyring(monkeypatch)
    store = _store(tmp_path)
    store.save(CookieJar())
    assert store.path.is_file()
    assert list(store.load()) == []
    assert keys

    with pytest.raises(AuthenticationError, match="passphrase is required"):
        store.export_file(tmp_path / "out", "")
    with pytest.raises(AuthenticationError, match="passphrase is required"):
        store.import_file(tmp_path / "out", "")


def test_cookie_file_import_merges_unrelated_existing_cookies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _keyring(monkeypatch)
    source = _store(tmp_path, "source")
    imported = CookieJar()
    imported.set_cookie(_cookie("from-export", "value"))
    source.save(imported)
    export = tmp_path / "cookies.export"
    source.export_file(export, "passphrase")

    target = _store(tmp_path, "target")
    existing = CookieJar()
    existing.set_cookie(_cookie("other", "keep", domain=".other.test"))
    target.save(existing)
    target.import_file(export, "passphrase")
    assert {cookie.name for cookie in target.load()} == {"other", "from-export"}


def test_browser_cookie_import_reports_missing_optional_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _keyring(monkeypatch)
    monkeypatch.setitem(sys.modules, "browser_cookie3", None)
    with pytest.raises(AuthenticationError, match="browser-cookie3"):
        _store(tmp_path).import_browser("example.test")


def test_browser_import_merges_and_preserves_corrupt_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _keyring(monkeypatch)
    store = _store(tmp_path)
    original = CookieJar()
    original.set_cookie(_cookie("other", "keep", domain=".other.test"))
    original.set_cookie(_cookie("session", "old", expires=int(time.time()) + 3600))
    store.save(original)
    browser = CookieJar()
    browser.set_cookie(_cookie("session", "new"))
    browser.set_cookie(_cookie("new", "browser"))
    monkeypatch.setitem(sys.modules, "browser_cookie3", SimpleNamespace(load=lambda **_kwargs: browser))
    store.import_browser("example.test")
    assert {(item.name, item.value) for item in store.load()} == {
        ("other", "keep"), ("session", "new"), ("new", "browser")
    }
    assert store.filesystem.exists(store.lock_relative)

    store.path.write_bytes(b"damaged")
    before = store.path.read_bytes()
    with pytest.raises(AuthenticationError, match="cannot be decrypted"):
        store.import_browser("example.test")
    assert store.path.read_bytes() == before


def test_cookie_delta_merges_stale_services_and_resolves_expiry_and_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _keyring(monkeypatch)
    store = _store(tmp_path)
    initial = CookieJar()
    expiry = int(time.time()) + 3600
    initial.set_cookie(_cookie("shared", "same", expires=expiry))
    initial.set_cookie(_cookie("remove", "old"))
    store.save(initial)
    first = store.load()
    second = store.load()
    baseline_first = store.snapshot(first)
    baseline_second = store.snapshot(second)

    first.set_cookie(_cookie("first", "1"))
    first.set_cookie(_cookie("shared", "same", expires=expiry + 3600))
    store.persist_delta(baseline_first, first)

    second.set_cookie(_cookie("second", "2"))
    second.set_cookie(_cookie("shared", "same"))
    second.clear(".example.test", "/", "remove")
    store.persist_delta(baseline_second, second)
    result = {item.name: item for item in store.load()}
    assert set(result) == {"shared", "first", "second"}
    assert result["shared"].expires == expiry + 3600

    third = store.load()
    baseline_third = store.snapshot(third)
    third.set_cookie(_cookie("shared", "different"))
    store.persist_delta(baseline_third, third)
    assert {item.name: item.value for item in store.load()}["shared"] == "different"

    fourth = store.load()
    baseline_fourth = store.snapshot(fourth)
    fourth.set_cookie(_cookie("shared", "different", expires=int(time.time()) - 1))
    store.persist_delta(baseline_fourth, fourth)
    assert "shared" not in {item.name for item in store.load()}


def test_unchanged_cookie_session_does_not_overwrite_another_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _keyring(monkeypatch)
    store = _store(tmp_path)
    stale = store.load()
    baseline = store.snapshot(stale)
    latest = CookieJar()
    latest.set_cookie(_cookie("new", "value"))
    store.save(latest)
    store.persist_delta(baseline, stale)
    assert [(item.name, item.value) for item in store.load()] == [("new", "value")]


def test_cookie_delta_merges_two_processes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(CookieStore, "_key", _fixed_key)
    root = str((tmp_path / "parallel").resolve())
    context = multiprocessing.get_context("spawn")
    ready = [context.Event(), context.Event()]
    start = context.Event()
    results = context.Queue()
    workers = [
        context.Process(target=_merge_in_child, args=(root, name, ready[index], start, results))
        for index, name in enumerate(("one", "two"))
    ]
    for worker in workers:
        worker.start()
    try:
        assert all(event.wait(10) for event in ready)
        start.set()
        assert [results.get(timeout=10) for _ in workers] == [None, None]
        for worker in workers:
            worker.join(timeout=10)
            assert worker.exitcode == 0
    finally:
        start.set()
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
            worker.join(timeout=5)
    assert {cookie.name for cookie in CookieStore(FileSystem(Path(root))).load()} == {"one", "two"}


def test_cookie_lock_timeout_does_not_change_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _keyring(monkeypatch)
    store = _store(tmp_path)
    jar = CookieJar()
    jar.set_cookie(_cookie("original", "value"))
    store.save(jar)
    before = store.path.read_bytes()
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    holder = context.Process(target=_hold_lock_in_child, args=(str(store.filesystem.path(store.lock_relative)), ready))
    holder.start()
    try:
        assert ready.wait(10)
        contender = CookieStore(store.filesystem, lock_timeout_seconds=0)
        with pytest.raises(InterProcessLockError, match="timed out"):
            contender.save(CookieJar())
    finally:
        holder.terminate()
        holder.join(timeout=10)
        assert not holder.is_alive()
    assert store.path.read_bytes() == before
    store.save(jar)
