from __future__ import annotations

import asyncio
import builtins
import json
import os
import sqlite3
import sys
import threading
from http.cookiejar import CookieJar
from pathlib import Path
from types import SimpleNamespace

import pytest
from support.vault import cookie_store

from image_downloader import AppConfig, AuthenticationError
from image_downloader.cli import build_parser
from image_downloader.commands import cookie as command
from image_downloader.commands.outcome import CommandOutcome
from image_downloader.commands.validation import validate_arguments
from image_downloader.exceptions import ArgumentError
from image_downloader.storage.cookies import _cookie


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "artificial-chrome"
    (root / "Default/Network").mkdir(parents=True)
    (root / "Default/Network/Cookies").write_bytes(b"SQLite format 3\x00artificial-db")
    (root / "Local State").write_text(json.dumps({"os_crypt": {"encrypted_key": "fictional-key"}}))
    return root


def test_explicit_source_never_discovers_and_filters_exact_domain(source, tmp_path, monkeypatch):
    seen = []
    jar = CookieJar()
    jar.set_cookie(_cookie("session", "fictional-value", domain=".example.test"))
    jar.set_cookie(_cookie("unrelated", "never-import", domain="example.test.attacker"))

    def chrome(**kwargs):
        seen.append(kwargs)
        assert Path(kwargs["cookie_file"]).read_bytes() == b"SQLite format 3\x00artificial-db"
        assert json.loads(Path(kwargs["key_file"]).read_text())["os_crypt"]["encrypted_key"] == "fictional-key"
        assert kwargs["domain_name"] == "example.test"
        return jar

    monkeypatch.setitem(sys.modules, "browser_cookie3", SimpleNamespace(chrome=chrome))
    store = cookie_store(tmp_path / "destination")
    before = (source / "Default/Network/Cookies").read_bytes()
    store.import_browser("example.test", chrome_user_data_dir=source)
    assert [(item.name, item.value) for item in store.load()] == [("session", "fictional-value")]
    assert (source / "Default/Network/Cookies").read_bytes() == before
    assert all(not Path(call["cookie_file"]).exists() and not Path(call["key_file"]).exists() for call in seen)


@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.parametrize("secure", [0, 1])
def test_sqlite_browser_security_flags_round_trip(source, tmp_path, monkeypatch, capsys, scoped, secure):
    # browser-cookie3 passes SQLite's integer is_secure directly to Cookie.
    with sqlite3.connect(":memory:") as database:
        flag = database.execute("SELECT ?", (secure,)).fetchone()[0]
    jar = CookieJar()
    original = _cookie("session", "fictional-browser-value", domain="example.test", secure=flag)
    jar.set_cookie(original)
    assert type(original.secure) is int
    monkeypatch.setitem(
        sys.modules, "browser_cookie3", SimpleNamespace(chrome=lambda **kwargs: jar, load=lambda **kwargs: jar)
    )
    store = cookie_store(tmp_path / "destination")
    store.import_browser("example.test", chrome_user_data_dir=source if scoped else None)
    imported = next(iter(store.load()))
    assert imported.secure is bool(secure)
    assert imported.value == "fictional-browser-value"
    assert type(original.secure) is int  # Never modify the dependency's object.
    assert "fictional-browser-value" not in store.path.read_text()
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("secure", [2, -1, None, "0", 0.0])
def test_invalid_browser_flags_are_rejected_before_storage(source, tmp_path, monkeypatch, capsys, secure):
    jar = CookieJar()
    jar.set_cookie(_cookie("session", "fictional-private-value", domain="example.test", secure=secure))
    monkeypatch.setitem(sys.modules, "browser_cookie3", SimpleNamespace(chrome=lambda **kwargs: jar))
    store = cookie_store(tmp_path / "destination")
    with pytest.raises(AuthenticationError, match="browser cookie data is invalid") as error:
        store.import_browser("example.test", chrome_user_data_dir=source)
    assert error.value.__suppress_context__
    assert not store.filesystem.root.exists()
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("kind", ["missing", "bad-db", "bad-state", "missing-key", "relative", "hardlink"])
def test_invalid_source_fails_before_loader(source, tmp_path, kind):
    if kind == "missing":
        (source / "Default/Network/Cookies").unlink()
    elif kind == "bad-db":
        (source / "Default/Network/Cookies").write_bytes(b"not-sqlite")
    elif kind == "bad-state":
        (source / "Local State").write_text("not-json")
    elif kind == "missing-key":
        (source / "Local State").write_text("{}")
    elif kind == "relative":
        source = Path("relative")
    else:
        os.link(source / "Local State", tmp_path / "hardlinked-state")
    store = cookie_store(tmp_path / "destination")
    with pytest.raises(AuthenticationError, match="source is invalid"):
        store.import_browser("example.test", chrome_user_data_dir=source)
    assert not store.path.exists()


def test_linked_source_is_rejected(source, tmp_path):
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(source, target_is_directory=True)
    except OSError:
        pytest.skip("creating symlinks requires platform permission")
    with pytest.raises(AuthenticationError, match="source is invalid"):
        cookie_store(tmp_path / "destination").import_browser("example.test", chrome_user_data_dir=linked)


def test_loader_failure_is_sanitized_and_has_no_fallback(source, tmp_path, monkeypatch, capsys):
    def fail(**kwargs):
        print("fictional-cookie-value")
        print("private-profile-path", file=sys.stderr)
        raise RuntimeError("fictional-cookie-value and private-profile-path")

    monkeypatch.setitem(sys.modules, "browser_cookie3", SimpleNamespace(chrome=fail))
    with pytest.raises(AuthenticationError, match="Chrome cookie import is unavailable") as error:
        cookie_store(tmp_path / "destination").import_browser("example.test", chrome_user_data_dir=source)
    assert error.value.__suppress_context__
    assert "fictional" not in str(error.value)
    assert capsys.readouterr() == ("", "")


def test_dependency_initialization_failure_is_sanitized(source, tmp_path, monkeypatch, capsys):
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "browser_cookie3":
            print("fictional-import-secret", file=sys.stderr)
            raise RuntimeError("fictional-import-secret and private-path")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    with pytest.raises(AuthenticationError, match="Chrome cookie import is unavailable") as error:
        cookie_store(tmp_path / "destination").import_browser("example.test", chrome_user_data_dir=source)
    assert error.value.__suppress_context__
    assert error.value.failure == "dependency_init"
    assert capsys.readouterr() == ("", "")


def test_app_bound_only_state_is_unsupported_before_loading(source, tmp_path):
    (source / "Local State").write_text(json.dumps({"os_crypt": {"app_bound_encrypted_key": "fictional-key"}}))
    with pytest.raises(AuthenticationError) as error:
        cookie_store(tmp_path / "destination").import_browser("example.test", chrome_user_data_dir=source)
    assert error.value.failure == "encryption"


@pytest.mark.parametrize(
    "kind,failure",
    [("missing", "source_missing"), ("bad-db", "database"), ("bad-state", "local_state")],
)
def test_source_failure_categories_are_distinct(source, tmp_path, kind, failure):
    if kind == "missing":
        (source / "Default/Network/Cookies").unlink()
    elif kind == "bad-db":
        (source / "Default/Network/Cookies").write_bytes(b"invalid")
    else:
        (source / "Local State").write_bytes(b"not-json")
    with pytest.raises(AuthenticationError) as error:
        cookie_store(tmp_path / "destination").import_browser("example.test", chrome_user_data_dir=source)
    assert error.value.failure == failure


def test_windows_scoped_cli_keeps_dependency_on_calling_thread(source, tmp_path, monkeypatch):
    """WMI may retain COM references until shutdown; never initialize it in a pool."""
    caller = threading.get_ident()
    seen = []
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "browser_cookie3":
            seen.append(threading.get_ident())
            if threading.get_ident() != caller:
                raise RuntimeError("fictional COM initialization failure in worker")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(command, "sys", SimpleNamespace(platform="win32"), raising=False)
    monkeypatch.setitem(sys.modules, "browser_cookie3", SimpleNamespace(chrome=lambda **kwargs: CookieJar()))
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    config = AppConfig(storage={"data_root": str(tmp_path / "data")})
    monkeypatch.setattr(command, "_config_for", lambda *args, **kwargs: (config, tmp_path, tmp_path, "test"))
    monkeypatch.setattr(command, "_persist_initial_user_config", lambda *args: None)
    args = build_parser().parse_args(
        ["cookie", "browser-import", "example.test", "--chrome-user-data-dir", str(source)]
    )
    asyncio.run(
        command.CookieCommandHandler().handle(validate_arguments(args), CommandOutcome("cookie", json_output=False))
    )
    assert seen == [caller]


def test_cli_forwards_explicit_source(source, tmp_path, monkeypatch):
    seen = []

    class Store:
        def __init__(self, filesystem, *, store):
            pass

        def import_browser(self, domain, **kwargs):
            seen.append((domain, kwargs))

    config = AppConfig(storage={"data_root": str(tmp_path / "data")})
    monkeypatch.setattr(command, "CookieStore", Store)
    monkeypatch.setattr(command, "_config_for", lambda *args, **kwargs: (config, tmp_path, tmp_path, "test"))
    monkeypatch.setattr(command, "_persist_initial_user_config", lambda *args: None)
    args = build_parser().parse_args(
        ["cookie", "browser-import", "example.test", "--chrome-user-data-dir", str(source)]
    )
    prepared = validate_arguments(args)
    asyncio.run(command.CookieCommandHandler().handle(prepared, CommandOutcome("cookie", json_output=False)))
    assert seen == [("example.test", {"chrome_user_data_dir": source})]


@pytest.mark.parametrize(
    "prefix", [["cookie", "export", "unused"], ["doctor"], ["--import-browser-cookies", "example.test"]]
)
def test_cli_rejects_source_outside_explicit_browser_import(source, prefix):
    args = build_parser().parse_args([*prefix, "--chrome-user-data-dir", str(source)])
    with pytest.raises(ArgumentError):
        validate_arguments(args)
