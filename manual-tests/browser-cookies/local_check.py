"""HUMAN ONLY: isolated Windows/Chrome test. Not a pytest entry point."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib.util
import io
import json
import logging
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from enum import IntEnum
from pathlib import Path

CI_VARIABLES = (
    "CI",
    "GITHUB_ACTIONS",
    "TF_BUILD",
    "GITLAB_CI",
    "JENKINS_URL",
    "BUILD_BUILDID",
    "CODEX_THREAD_ID",
    "CODEX_CI",
)
REPOSITORY = Path(__file__).resolve().parents[2]


class ImportResult(IntEnum):
    SUCCESS = 0
    FAILED = 1
    UNSUPPORTED = 2
    CONFIGURATION = 3
    DEPENDENCY_INIT = 4
    SOURCE_MISSING = 5
    SOURCE_UNSAFE = 6
    SOURCE_CHANGED = 7
    SOURCE_ACCESS = 8
    SOURCE_IO = 9
    DATABASE = 10
    LOCAL_STATE = 11
    ENCRYPTION = 12
    KEYRING = 13
    STORAGE = 14
    COOKIE_DATA = 15


IMPORT_MESSAGES = {
    ImportResult.FAILED: "FAIL LOCAL_IMPORT",
    ImportResult.UNSUPPORTED: "UNSUPPORTED CHROME_IMPORT",
    ImportResult.CONFIGURATION: "FAIL LOCAL_CONFIGURATION",
    ImportResult.DEPENDENCY_INIT: "FAIL LOCAL_DEPENDENCY_INIT",
    ImportResult.SOURCE_MISSING: "FAIL LOCAL_SOURCE_MISSING",
    ImportResult.SOURCE_UNSAFE: "FAIL LOCAL_SOURCE_UNSAFE",
    ImportResult.SOURCE_CHANGED: "FAIL LOCAL_SOURCE_CHANGED",
    ImportResult.SOURCE_ACCESS: "FAIL LOCAL_SOURCE_ACCESS",
    ImportResult.SOURCE_IO: "FAIL LOCAL_SOURCE_IO",
    ImportResult.DATABASE: "FAIL LOCAL_COOKIE_DATABASE",
    ImportResult.LOCAL_STATE: "FAIL LOCAL_STATE",
    ImportResult.ENCRYPTION: "UNSUPPORTED CHROME_ENCRYPTION",
    ImportResult.KEYRING: "FAIL LOCAL_KEYRING",
    ImportResult.STORAGE: "FAIL LOCAL_STORAGE",
    ImportResult.COOKIE_DATA: "FAIL LOCAL_COOKIE_DATA",
}


@contextlib.contextmanager
def quiet():
    with io.StringIO() as output, io.StringIO() as errors:
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            yield


def permitted(explicit: bool) -> bool:
    # These checks prevent mistakes. Only OS/environment separation prevents AI access.
    return (
        explicit
        and sys.platform == "win32"
        and sys.stdin.isatty()
        and sys.stdout.isatty()
        and not any(os.environ.get(name) for name in CI_VARIABLES)
    )


def owned(work: Path, identity: str) -> bool:
    from image_downloader.storage.path_safety import existing_directory, existing_regular_file

    try:
        existing_directory(work, "local test", required=True)
        marker = existing_regular_file(work / ".browser-lab.json", "local marker", required=True)
        return marker is not None and json.loads(marker.read_text(encoding="utf-8")) == {"identity": identity}
    except Exception:
        return False


def clean(work: Path, identity: str, account: str) -> None:
    if not owned(work, identity):
        raise ValueError
    from image_downloader.storage.path_safety import is_link_or_reparse

    # Do not recursively remove a tree changed to contain links/reparse points.
    for current, directories, files in os.walk(work, followlinks=False):
        if any(is_link_or_reparse(Path(current) / name) for name in directories + files):
            raise ValueError
    import keyring

    if keyring.get_password("image-downloader.cookie-key", account) is not None:
        keyring.delete_password("image-downloader.cookie-key", account)
    shutil.rmtree(work)


def core_compatible() -> bool:
    try:
        from image_downloader.storage.browser_cookies import ChromeImportFailure
        from image_downloader.storage.cookies import _normalize_browser_cookies

        return callable(_normalize_browser_cookies) and ChromeImportFailure.DEPENDENCY_INIT.value == "dependency_init"
    except (ImportError, AttributeError):
        return False


def authentication_result(error: Exception) -> ImportResult:
    from image_downloader.storage.browser_cookies import ChromeCookieImportError, ChromeImportFailure

    if isinstance(error, ChromeCookieImportError):
        if error.failure in {ChromeImportFailure.DEPENDENCY_MISSING, ChromeImportFailure.LOADER}:
            return ImportResult.UNSUPPORTED
        return ImportResult[error.failure.name]
    return {
        "could not access cookie encryption key in the OS credential store": ImportResult.KEYRING,
        "could not persist encrypted cookie file": ImportResult.STORAGE,
        "browser cookie data is invalid": ImportResult.COOKIE_DATA,
    }.get(str(error), ImportResult.FAILED)


def import_with_cli(config: Path, profile: Path) -> int:
    from image_downloader import AuthenticationError, ConfigurationError
    from image_downloader.cli import build_parser
    from image_downloader.commands.cookie import CookieCommandHandler
    from image_downloader.commands.outcome import CommandOutcome
    from image_downloader.commands.validation import validate_arguments

    # Application presentation and dependency diagnostics never escape this process.
    with io.StringIO() as output, io.StringIO() as errors:
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            arguments = build_parser().parse_args(
                [
                    "cookie",
                    "browser-import",
                    "127.0.0.1",
                    "--chrome-user-data-dir",
                    str(profile),
                    "--config",
                    str(config),
                    "--profile",
                    "browser-lab",
                ]
            )
            try:
                prepared = validate_arguments(arguments)
                return asyncio.run(CookieCommandHandler().handle(prepared, CommandOutcome("cookie", json_output=False)))
            except ConfigurationError:
                return ImportResult.CONFIGURATION
            except AuthenticationError as error:
                return authentication_result(error)
            except Exception:
                return ImportResult.FAILED


def cookie_ready(site) -> bool:
    try:
        state = site.cookie_status()
    except Exception:
        print("FAIL LOCAL_SITE_VERSION")
        return False
    if state != "ready":
        print("FAIL LOCAL_COOKIE_EXPIRED" if state == "expired" else "FAIL LOCAL_COOKIE_NOT_ISSUED")
        return False
    print("PASS DUMMY_COOKIE_ISSUED")
    return True


def reload_cookie(store, token: str):
    """Verify the persisted target before attempting any HTTP request."""
    from image_downloader import AuthenticationError

    try:
        with quiet():
            exists = store.filesystem.exists(store.relative)
            jar = store.load() if exists else None
    except AuthenticationError as error:
        code = (
            "FAIL LOCAL_KEYRING" if authentication_result(error) == ImportResult.KEYRING else "FAIL LOCAL_COOKIE_RELOAD"
        )
        print(code)
        return None
    except Exception:
        print("FAIL LOCAL_COOKIE_RELOAD")
        return None
    if not exists:
        print("FAIL LOCAL_COOKIE_NOT_IMPORTED")
        return None
    if not jar:
        print("FAIL LOCAL_COOKIE_EMPTY")
        return None
    targets = [
        cookie
        for cookie in jar
        if cookie.name == "lab_session" and cookie.domain.lstrip(".") == "127.0.0.1" and cookie.path == "/"
    ]
    if not targets:
        print("FAIL LOCAL_COOKIE_MISSING")
        return None
    # compare_digest(str, str) raises on non-ASCII values. Compare UTF-8 bytes so
    # an unrelated or malformed value produces a mismatch, never a raw exception.
    if not any(
        secrets.compare_digest((cookie.value or "").encode("utf-8"), token.encode("utf-8")) for cookie in targets
    ):
        print("FAIL LOCAL_COOKIE_MISMATCH")
        return None
    print("PASS ENCRYPTED_ROUND_TRIP")
    return jar


def check_loopback(jar, origin: str) -> bool:
    try:
        with quiet():
            import httpx

            with httpx.Client(cookies=jar, trust_env=False, follow_redirects=False) as client:
                response = client.get(f"{origin}/cookie-check")
    except Exception:
        print("FAIL LOCAL_LOOPBACK_TRANSPORT")
        return False
    try:
        valid = response.status_code == 200 and response.json() == {"ok": True}
    except Exception:
        valid = False
    if not valid:
        print("FAIL LOCAL_LOOPBACK_AUTHENTICATION")
        return False
    print("PASS LOOPBACK_AUTHENTICATION")
    return True


def _site_class():
    source = REPOSITORY / "examples/plugin-v3-feature-lab/server.py"
    spec = importlib.util.spec_from_file_location("isolated_cookie_site", source)
    if spec is None or spec.loader is None:
        raise ValueError
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.LabSite


def exercise(work: Path, chrome: Path) -> int:
    from image_downloader.storage import FileSystem
    from image_downloader.storage.cookies import CookieStore
    from image_downloader.storage.path_safety import existing_directory, existing_regular_file

    work = existing_directory(work, "local test")
    existing_regular_file(chrome, "Chrome executable", required=True)
    if work.exists() or work.is_relative_to(REPOSITORY) or any((parent / ".git").exists() for parent in work.parents):
        raise ValueError
    if input("Confirm AI-free isolated environment and disconnected external network: type ISOLATED: ") != "ISOLATED":
        raise ValueError
    identity = secrets.token_hex(32)
    work.mkdir(parents=True)
    (work / ".browser-lab.json").write_text(json.dumps({"identity": identity}), encoding="utf-8")
    profile = work / "chrome-profile"
    profile.mkdir()
    scratch = work / "temporary"
    scratch.mkdir()
    config = work / "app.yaml"
    config.write_text(
        json.dumps(
            {
                "profile": {"default": "browser-lab"},
                "storage": {"data_root": str(work / "data")},
                "plugins": {"root": str(work / "plugins")},
            }
        ),
        encoding="utf-8",
    )
    store = CookieStore(FileSystem(work / "data/profiles/browser-lab/cookie"))
    previous_temp = tempfile.tempdir
    previous_environment = {name: os.environ.get(name) for name in ("TEMP", "TMP", "TMPDIR")}
    tempfile.tempdir = str(scratch)
    os.environ.update({name: str(scratch) for name in previous_environment})
    status = 1
    browser = None
    try:
        with _site_class()() as site:
            browser = subprocess.Popen(
                [
                    str(chrome),
                    f"--user-data-dir={profile}",
                    "--no-first-run",
                    "--disable-sync",
                    "--no-default-browser-check",
                    f"{site.origin}/cookie-login",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print("PASS DEDICATED_BROWSER_STARTED")
            if input("Wait for the local page, close dedicated Chrome completely, then type CLOSED: ") != "CLOSED":
                raise ValueError
            browser.wait(timeout=15)
            if not owned(work, identity):
                raise ValueError
            # Validate inside the owned tree before the core takes stable snapshots.
            existing_directory(profile, "dedicated Chrome", required=True)
            if not cookie_ready(site):
                return 1
            result = import_with_cli(config, profile)
            if result != 0:
                print(IMPORT_MESSAGES.get(result, "FAIL LOCAL_IMPORT"))
                status = 2 if result in {ImportResult.UNSUPPORTED, ImportResult.ENCRYPTION} else 1
            else:
                print("PASS CORE_IMPORT")
                jar = reload_cookie(store, site.cookie_token)
                if jar is not None and check_loopback(jar, site.origin):
                    status = 0
    except (Exception, KeyboardInterrupt):
        print("FAIL LOCAL_BROWSER_CHECK")
        status = 1
    finally:
        tempfile.tempdir = previous_temp
        for name, value in previous_environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        try:
            if input("Close dedicated Chrome; delete ONLY this test's data and key: type DELETE: ") == "DELETE":
                if browser is not None:
                    browser.wait(timeout=15)
                with quiet():
                    clean(work, identity, store.account)
                print("PASS LOCAL_CLEANUP")
            else:
                print("FAIL CLEANUP_PENDING")
                status = 1
        except (Exception, KeyboardInterrupt):
            print("FAIL CLEANUP_PENDING")
            status = 1
    return status


class LocalParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        print("FAIL LOCAL_SETUP")
        raise SystemExit(1)


def main() -> int:
    parser = LocalParser(description=__doc__)
    parser.add_argument("--real-browser-test", action="store_true")
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--chrome-executable", type=Path, required=True)
    args = parser.parse_args()
    if not permitted(args.real_browser_test):
        print("FAIL LOCAL_INTERACTIVE_ONLY")
        return 1
    if not core_compatible():
        print("FAIL LOCAL_PACKAGE_VERSION")
        return 1
    previous_logging = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        return exercise(args.work_dir, args.chrome_executable)
    except (Exception, KeyboardInterrupt):
        print("FAIL LOCAL_SETUP")
        return 1
    finally:
        logging.disable(previous_logging)


if __name__ == "__main__":
    raise SystemExit(main())
