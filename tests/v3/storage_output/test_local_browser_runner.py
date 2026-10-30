"""Only fake modules, artificial files and in-memory cookies; never launch Chrome."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def runner():
    spec = importlib.util.spec_from_file_location(
        "local_browser_runner_test", ROOT / "manual-tests/browser-cookies/local_check.py"
    )
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


@pytest.mark.parametrize(
    "explicit,tty,ci", [(False, True, False), (True, False, False), (True, True, True), (True, True, False)]
)
def test_runner_requires_explicit_interactive_non_ci_execution(runner, monkeypatch, explicit, tty, ci):
    for key in runner.CI_VARIABLES:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(
        runner,
        "sys",
        SimpleNamespace(
            platform="win32", stdin=SimpleNamespace(isatty=lambda: tty), stdout=SimpleNamespace(isatty=lambda: tty)
        ),
    )
    if ci:
        monkeypatch.setenv("CI", "true")
    assert runner.permitted(explicit) is (explicit and tty and not ci)


def test_marker_rejects_unowned_or_changed_directory(runner, tmp_path):
    assert not runner.owned(tmp_path, "identity")
    (tmp_path / ".browser-lab.json").write_text(json.dumps({"identity": "other"}))
    assert not runner.owned(tmp_path, "identity")
    (tmp_path / ".browser-lab.json").write_text(json.dumps({"identity": "identity"}))
    assert runner.owned(tmp_path, "identity")


def test_main_refuses_before_any_browser_or_keyring_call(runner, monkeypatch, capsys):
    monkeypatch.setattr(
        sys, "argv", ["local_check.py", "--real-browser-test", "--work-dir", "unused", "--chrome-executable", "unused"]
    )
    monkeypatch.setattr(runner, "permitted", lambda explicit: False)
    monkeypatch.setattr(runner, "exercise", lambda *args: pytest.fail("must not reach real exercise"))
    assert runner.main() == 1
    assert capsys.readouterr().out == "FAIL LOCAL_INTERACTIVE_ONLY\n"


def test_cli_check_captures_raw_output_and_passes_explicit_source(runner, monkeypatch, tmp_path, capsys):
    from image_downloader.commands import cookie as command

    seen = []

    async def fake_handle(self, prepared, outcome):
        seen.append(prepared.arguments.chrome_user_data_dir)
        print("fictional-cookie-value and private-path")
        print("fictional-key", file=sys.stderr)
        return 0

    monkeypatch.setattr(command.CookieCommandHandler, "handle", fake_handle)
    (tmp_path / "chrome-profile").mkdir()
    assert runner.import_with_cli(tmp_path / "app.yaml", tmp_path / "chrome-profile") == 0
    assert seen == [tmp_path / "chrome-profile"]
    assert capsys.readouterr() == ("", "")


def test_missing_alternate_profile_is_classified_before_any_cookie_read(runner, tmp_path, capsys):
    profile = tmp_path / "artificial-profile"
    profile.mkdir()
    config = tmp_path / "app.yaml"
    config.write_text(
        json.dumps({"storage": {"data_root": str(tmp_path / "data")}, "plugins": {"root": str(tmp_path / "plugins")}}),
        encoding="utf-8",
    )
    # Reproduce the old runner's default/CLI mismatch. The default browser/keyring
    # guards would fail this test if a real provider or host file were reached.
    assert runner.import_with_cli(config, profile) == 3
    assert capsys.readouterr() == ("", "")


def test_main_rejects_stale_package_before_starting_browser(runner, monkeypatch, capsys):
    monkeypatch.setattr(
        sys, "argv", ["local_check.py", "--real-browser-test", "--work-dir", "unused", "--chrome-executable", "unused"]
    )
    monkeypatch.setattr(runner, "permitted", lambda explicit: True)
    monkeypatch.setattr(runner, "core_compatible", lambda: False)
    monkeypatch.setattr(runner, "exercise", lambda *args: pytest.fail("must reject before exercise"))
    assert runner.main() == 1
    assert capsys.readouterr() == ("FAIL LOCAL_PACKAGE_VERSION\n", "")


def test_core_guard_requires_sqlite_flag_normalization_fix(runner, monkeypatch):
    assert runner.core_compatible()
    monkeypatch.setitem(sys.modules, "image_downloader.storage.cookies", SimpleNamespace())
    assert not runner.core_compatible()


@pytest.mark.parametrize(
    "failure,result",
    [
        ("source_missing", "SOURCE_MISSING"),
        ("source_unsafe", "SOURCE_UNSAFE"),
        ("source_changed", "SOURCE_CHANGED"),
        ("source_access", "SOURCE_ACCESS"),
        ("source_io", "SOURCE_IO"),
        ("database", "DATABASE"),
        ("local_state", "LOCAL_STATE"),
        ("encryption", "ENCRYPTION"),
        ("dependency_missing", "UNSUPPORTED"),
        ("dependency_init", "DEPENDENCY_INIT"),
        ("loader", "UNSUPPORTED"),
    ],
)
def test_cli_failure_categories_never_publish_raw_diagnostics(runner, monkeypatch, tmp_path, capsys, failure, result):
    from image_downloader.commands import cookie as command
    from image_downloader.storage.browser_cookies import ChromeCookieImportError, ChromeImportFailure

    async def fail(*args):
        print("fictional-private-value", file=sys.stderr)
        raise ChromeCookieImportError(ChromeImportFailure(failure))

    monkeypatch.setattr(command.CookieCommandHandler, "handle", fail)
    (tmp_path / "source").mkdir()
    assert runner.import_with_cli(tmp_path / "app.yaml", tmp_path / "source") == runner.ImportResult[result]
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    "message,result",
    [
        ("could not access cookie encryption key in the OS credential store", "KEYRING"),
        ("could not persist encrypted cookie file", "STORAGE"),
        ("browser cookie data is invalid", "COOKIE_DATA"),
        ("fictional-private-value", "FAILED"),
    ],
)
def test_keyring_storage_and_unknown_failures_are_not_source_errors(runner, message, result):
    from image_downloader import AuthenticationError

    assert runner.authentication_result(AuthenticationError(message)) == runner.ImportResult[result]


@pytest.mark.parametrize(
    "state,expected,message",
    [
        ("ready", True, "PASS DUMMY_COOKIE_ISSUED"),
        ("missing", False, "FAIL LOCAL_COOKIE_NOT_ISSUED"),
        ("expired", False, "FAIL LOCAL_COOKIE_EXPIRED"),
        (None, False, "FAIL LOCAL_SITE_VERSION"),
    ],
)
def test_cookie_issuance_is_checked_before_import(runner, capsys, state, expected, message):
    site = SimpleNamespace(cookie_status=lambda: state) if state is not None else SimpleNamespace()
    assert runner.cookie_ready(site) is expected
    assert capsys.readouterr() == (message + "\n", "")


@pytest.mark.parametrize(
    "kind,message",
    [
        ("not-imported", "FAIL LOCAL_COOKIE_NOT_IMPORTED"),
        ("empty", "FAIL LOCAL_COOKIE_EMPTY"),
        ("missing", "FAIL LOCAL_COOKIE_MISSING"),
        ("mismatch", "FAIL LOCAL_COOKIE_MISMATCH"),
        ("non-ascii", "FAIL LOCAL_COOKIE_MISMATCH"),
        ("reload-error", "FAIL LOCAL_COOKIE_RELOAD"),
        ("keyring-error", "FAIL LOCAL_KEYRING"),
        ("unrelated-non-ascii", "PASS ENCRYPTED_ROUND_TRIP"),
    ],
)
def test_reload_distinguishes_empty_mismatch_and_errors(runner, monkeypatch, tmp_path, capsys, kind, message):
    from http.cookiejar import CookieJar

    from image_downloader import AuthenticationError
    from image_downloader.storage import FileSystem
    from image_downloader.storage.cookies import CookieStore, _cookie

    monkeypatch.setattr(CookieStore, "_key", lambda *args, **kwargs: b"k" * 32)
    store = CookieStore(FileSystem(tmp_path / "cookie"))
    jar = CookieJar()
    token = "fictional-cookie-value"
    if kind not in {"empty", "not-imported"}:
        name = "other" if kind == "missing" else "lab_session"
        value = (
            "dummy-non-ASCII-\u65e5\u672c\u8a9e" if kind == "non-ascii" else "wrong" if kind == "mismatch" else token
        )
        jar.set_cookie(_cookie(name, value, domain="127.0.0.1"))
    if kind == "unrelated-non-ascii":
        jar.set_cookie(_cookie("other", "dummy-non-ASCII-\u65e5\u672c\u8a9e", domain="127.0.0.1"))
    if kind != "not-imported":
        store.save(jar)

    def failed_load():
        print("fictional-private-key", file=sys.stderr)
        if kind == "keyring-error":
            raise AuthenticationError("could not access cookie encryption key in the OS credential store")
        raise AuthenticationError("encrypted cookie file cannot be decrypted")

    if kind in {"reload-error", "keyring-error"}:
        monkeypatch.setattr(store, "load", failed_load)
    actual = runner.reload_cookie(store, token)
    assert (actual is not None) is (kind == "unrelated-non-ascii")
    assert capsys.readouterr() == (message + "\n", "")


@pytest.mark.parametrize(
    "kind,expected,message",
    [
        ("success", True, "PASS LOOPBACK_AUTHENTICATION"),
        ("transport", False, "FAIL LOCAL_LOOPBACK_TRANSPORT"),
        ("denied", False, "FAIL LOCAL_LOOPBACK_AUTHENTICATION"),
        ("bad-json", False, "FAIL LOCAL_LOOPBACK_AUTHENTICATION"),
    ],
)
def test_loopback_failure_is_separate_and_sanitized(runner, monkeypatch, capsys, kind, expected, message):
    from http.cookiejar import CookieJar

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False

        def __enter__(self):
            print("fictional-private-cookie", file=sys.stderr)
            if kind == "transport":
                raise RuntimeError("fictional-private-cookie")
            return self

        def __exit__(self, *args):
            pass

        def get(self, url):
            assert url == "http://127.0.0.1:45678/cookie-check"
            return SimpleNamespace(status_code=401 if kind == "denied" else 200, json=self.payload)

        def payload(self):
            if kind == "bad-json":
                raise ValueError("fictional-response-body")
            return {"ok": True}

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(Client=Client))
    assert runner.check_loopback(CookieJar(), "http://127.0.0.1:45678") is expected
    assert capsys.readouterr() == (message + "\n", "")


@pytest.mark.parametrize(
    "import_code,valid,expected",
    [
        (0, True, 0),
        (0, False, 1),
        (0, None, 1),
        (0, "teardown", 1),
        (1, True, 1),
        (2, True, 2),
        (3, True, 1),
        (4, True, 1),
        (5, True, 1),
        (12, True, 2),
    ],
)
def test_exercise_uses_only_fake_browser_cookie_and_keyring(
    runner, monkeypatch, tmp_path, capsys, import_code, valid, expected
):
    """Simulate the HUMAN runner without a browser, decryption or network."""
    from image_downloader.storage.cookies import _cookie

    keys = {}
    monkeypatch.setitem(
        sys.modules,
        "keyring",
        SimpleNamespace(
            get_password=lambda service, account: keys.get((service, account)),
            set_password=lambda service, account, value: keys.__setitem__((service, account), value),
            delete_password=lambda service, account: keys.pop((service, account)),
        ),
    )
    fake_site = SimpleNamespace(
        origin="http://127.0.0.1:45678", cookie_token="fictional-manual-cookie", cookie_status=lambda: "ready"
    )

    class Site:
        def __enter__(self):
            return fake_site

        def __exit__(self, *args):
            if valid == "teardown":
                raise RuntimeError("fictional-private-teardown-failure")

    monkeypatch.setattr(runner, "_site_class", lambda: Site)

    def fake_chrome_process(arguments, **kwargs):
        profile = Path(
            next(arg.removeprefix("--user-data-dir=") for arg in arguments if arg.startswith("--user-data-dir="))
        )
        (profile / "Default/Network").mkdir(parents=True)
        (profile / "Default/Network/Cookies").write_bytes(b"SQLite format 3\x00artificial-db")
        (profile / "Local State").write_text(json.dumps({"os_crypt": {"encrypted_key": "fictional-key"}}))
        return SimpleNamespace(wait=lambda **kwargs: None)

    monkeypatch.setattr(runner.subprocess, "Popen", fake_chrome_process)
    answers = iter(["ISOLATED", "CLOSED", "DELETE"])
    monkeypatch.setattr("builtins.input", lambda *args: next(answers))
    executable = tmp_path / "fake-chrome.exe"
    executable.write_bytes(b"artificial executable, never executed")
    work = tmp_path / "local-work"
    real_import_with_cli = runner.import_with_cli
    imported_calls = []

    def fake_browser_loader(**kwargs):
        from http.cookiejar import CookieJar

        imported_calls.append(kwargs)
        assert kwargs["domain_name"] == "127.0.0.1"
        assert Path(kwargs["cookie_file"]).is_relative_to(work / "temporary")
        assert Path(kwargs["key_file"]).is_relative_to(work / "temporary")
        jar = CookieJar()
        if valid is not None:
            jar.set_cookie(
                _cookie(
                    "lab_session",
                    fake_site.cookie_token if valid else "wrong-fictional-value",
                    domain="127.0.0.1",
                    secure=0,
                )
            )
        return jar

    def imported(config, profile):
        # Keep CLI, source checks, snapshots, merge and encrypted persistence real.
        if import_code == 0:
            return real_import_with_cli(config, profile)
        return import_code

    monkeypatch.setitem(sys.modules, "browser_cookie3", SimpleNamespace(chrome=fake_browser_loader))
    monkeypatch.setattr(runner, "import_with_cli", imported)

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False
            assert [cookie.value for cookie in kwargs["cookies"]] == [fake_site.cookie_token]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url):
            assert url == fake_site.origin + "/cookie-check"
            return SimpleNamespace(status_code=200, json=lambda: {"ok": True})

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(Client=Client))
    assert runner.exercise(work, executable) == expected
    assert len(imported_calls) == (1 if import_code == 0 else 0)
    output = capsys.readouterr()
    assert "PASS LOCAL_CLEANUP" in output.out
    expected_message = runner.IMPORT_MESSAGES.get(import_code) or {
        None: "PASS CORE_IMPORT\nFAIL LOCAL_COOKIE_NOT_IMPORTED",
        False: "PASS CORE_IMPORT\nFAIL LOCAL_COOKIE_MISMATCH",
        "teardown": "FAIL LOCAL_BROWSER_CHECK",
    }.get(valid)
    if expected_message:
        assert expected_message in output.out
    assert "fictional" not in output.out and str(tmp_path) not in output.out
    assert not output.err
    assert not work.exists()
    assert not keys
