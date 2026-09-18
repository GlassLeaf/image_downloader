from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from image_downloader.auth.provider import CookieStore, _jar_from_pairs, _make_cookie
from image_downloader.exceptions import AuthenticationError
from image_downloader.observability.events import EventBus


def _keyring(monkeypatch):
    import keyring

    values: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(keyring, "get_password", lambda service, account: values.get((service, account)))
    monkeypatch.setattr(
        keyring, "set_password", lambda service, account, value: values.__setitem__((service, account), value)
    )
    return values


def _header(jar, url: str) -> str:
    cookies = httpx.Cookies(jar)
    request = httpx.Request("GET", url)
    cookies.set_cookie_header(request)
    return request.headers.get("cookie", "")


def test_cookie_jar_limits_domain_path_and_secure_delivery() -> None:
    jar = _jar_from_pairs({"host": "value"}, "https://example.test/path")
    jar.set_cookie(_make_cookie("admin", "only", "example.test", path="/admin", domain_specified=True))
    jar.set_cookie(_make_cookie("secure", "yes", "example.test", secure=True, domain_specified=True))
    assert "admin=only" in _header(jar, "https://example.test/admin/page")
    assert "admin=only" not in _header(jar, "https://example.test/public")
    assert "secure=yes" not in _header(jar, "http://example.test/public")
    assert _header(jar, "https://other.test/") == ""


def test_cookie_file_is_encrypted_and_keyring_holds_only_key(tmp_path: Path, monkeypatch) -> None:
    values = _keyring(monkeypatch)
    store = CookieStore(tmp_path / "cookies.enc")
    store.save(_jar_from_pairs({"session": "secret-value"}, "https://example.test"))
    payload = store.path.read_text(encoding="utf-8")
    assert "secret-value" not in payload and "ciphertext" in payload
    assert len(values) == 1
    assert _header(store.load(), "https://example.test/") == "session=secret-value"


def test_tampered_cookie_file_and_missing_key_fail(tmp_path: Path, monkeypatch) -> None:
    values = _keyring(monkeypatch)
    store = CookieStore(tmp_path / "cookies.enc")
    store.save(_jar_from_pairs({"session": "secret"}, "https://example.test"))
    data = json.loads(store.path.read_text(encoding="utf-8"))
    data["ciphertext"] = "AAAA"
    store.path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(AuthenticationError):
        store.load()
    values.clear()
    with pytest.raises(AuthenticationError):
        store.load()


def test_legacy_cookie_data_is_ignored(tmp_path: Path, monkeypatch) -> None:
    _keyring(monkeypatch)
    legacy = tmp_path / "cookies.json"
    legacy.write_text('{"session":"legacy"}', encoding="utf-8")
    store = CookieStore(tmp_path / "cookies.enc")
    assert list(store.load()) == []
    assert legacy.exists()


def test_legacy_keyring_entry_is_not_inspected(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []
    import keyring

    monkeypatch.setattr(keyring, "get_password", lambda service, account: calls.append(service) or None)
    store = CookieStore(tmp_path / "cookies.enc")
    assert list(store.load()) == []
    assert "image-downloader.cookies" not in calls


def test_cookie_save_event_reaches_event_loop_from_worker_thread(tmp_path: Path, monkeypatch) -> None:
    _keyring(monkeypatch)

    async def scenario() -> list[str]:
        actions: list[str] = []
        events = EventBus()
        events.on("on_cookie_store_access", lambda payload: actions.append(payload["action"]))
        store = CookieStore(tmp_path / "cookies.enc", events=events, event_loop=asyncio.get_running_loop())
        await asyncio.to_thread(store.save, _jar_from_pairs({"session": "secret"}, "https://example.test"))
        await asyncio.sleep(0)
        return actions

    assert "save" in asyncio.run(scenario())


def test_export_import_merges_and_replaces_matching_cookie(tmp_path: Path, monkeypatch) -> None:
    _keyring(monkeypatch)
    source = CookieStore(tmp_path / "source" / "cookies.enc")
    source.save(_jar_from_pairs({"session": "new"}, "https://example.test"))
    export = tmp_path / "cookies.export"
    source.export_file(export, "passphrase")
    target = CookieStore(tmp_path / "target" / "cookies.enc")
    local = _jar_from_pairs({"session": "old", "other": "keep"}, "https://example.test")
    target.save(local)
    imported = target.import_file(export, "passphrase")
    assert "session=new" in _header(imported, "https://example.test/")
    assert "other=keep" in _header(imported, "https://example.test/")
    with pytest.raises(AuthenticationError):
        target.import_file(export, "wrong")
