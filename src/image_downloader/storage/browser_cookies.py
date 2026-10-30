"""Explicit Chrome source loading; never discover another browser or profile."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
from enum import StrEnum
from http.cookiejar import CookieJar
from pathlib import Path

from ..exceptions import AuthenticationError, ConfigurationError, StorageSafetyError
from .filesystem import FileSystem
from .path_safety import existing_directory
from .reading import VerificationFileChangedError, stable_file


class ChromeImportFailure(StrEnum):
    SOURCE_MISSING = "source_missing"
    SOURCE_UNSAFE = "source_unsafe"
    SOURCE_CHANGED = "source_changed"
    SOURCE_ACCESS = "source_access"
    SOURCE_IO = "source_io"
    DATABASE = "database"
    LOCAL_STATE = "local_state"
    ENCRYPTION = "encryption"
    DEPENDENCY_MISSING = "dependency_missing"
    DEPENDENCY_INIT = "dependency_init"
    LOADER = "loader"


class ChromeCookieImportError(AuthenticationError):
    """Expose an allow-listed failure category without source data or raw errors."""

    def __init__(self, failure: ChromeImportFailure) -> None:
        self.failure = failure
        if failure == ChromeImportFailure.DEPENDENCY_MISSING:
            message = "browser cookie import requires browser-cookie3"
        elif failure in {
            ChromeImportFailure.DEPENDENCY_INIT,
            ChromeImportFailure.ENCRYPTION,
            ChromeImportFailure.LOADER,
        }:
            message = "Chrome cookie import is unavailable"
        else:
            message = "Chrome cookie source is invalid or changed"
        super().__init__(message)


def _validate_state(payload: bytes) -> None:
    try:
        if len(payload) > 4 * 1024 * 1024:
            raise ValueError
        crypt = json.loads(payload)["os_crypt"]
        key = crypt.get("encrypted_key")
        if isinstance(key, str) and key:
            return
        # An App-Bound-only key is unsupported, not a corrupt source. Do not
        # disable protection, invoke an elevated service, or search elsewhere.
        if crypt.get("app_bound_encrypted_key"):
            raise ChromeCookieImportError(ChromeImportFailure.ENCRYPTION)
        raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise ChromeCookieImportError(ChromeImportFailure.LOCAL_STATE) from None


def import_chrome(domain: str, user_data_dir: Path) -> CookieJar:
    """Load a closed Chrome Default profile from stable, explicit snapshots.

    Windows is the documented real-browser target. Snapshot validation is also
    exercised with artificial files and a fake loader on every CI platform.
    """
    try:
        root = existing_directory(user_data_dir, "Chrome source", required=True)
        filesystem = FileSystem(root)
        # Hold both checked source handles until the snapshots are complete.
        with stable_file(filesystem, Path("Default/Network/Cookies")) as cookies:
            with stable_file(filesystem, Path("Local State")) as state:
                if cookies.read(16) != b"SQLite format 3\x00":
                    raise ChromeCookieImportError(ChromeImportFailure.DATABASE)
                cookies.seek(0)
                payload = state.read(4 * 1024 * 1024 + 1)
                _validate_state(payload)
                with tempfile.TemporaryDirectory(prefix="image-downloader-chrome-") as temporary:
                    directory = Path(temporary)
                    cookie_file, key_file = directory / "Cookies", directory / "Local State"
                    with cookie_file.open("wb") as destination:
                        shutil.copyfileobj(cookies, destination)
                    key_file.write_bytes(payload)
                    jar = _load(domain, cookie_file, key_file)
        return jar
    except FileNotFoundError:
        raise ChromeCookieImportError(ChromeImportFailure.SOURCE_MISSING) from None
    except PermissionError:
        raise ChromeCookieImportError(ChromeImportFailure.SOURCE_ACCESS) from None
    except VerificationFileChangedError:
        raise ChromeCookieImportError(ChromeImportFailure.SOURCE_CHANGED) from None
    except (ConfigurationError, StorageSafetyError):
        raise ChromeCookieImportError(ChromeImportFailure.SOURCE_UNSAFE) from None
    except OSError:
        raise ChromeCookieImportError(ChromeImportFailure.SOURCE_IO) from None


def _load(domain: str, cookie_file: Path, key_file: Path) -> CookieJar:
    with io.StringIO() as output, io.StringIO() as errors:
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            try:
                import browser_cookie3
            except ImportError:
                raise ChromeCookieImportError(ChromeImportFailure.DEPENDENCY_MISSING) from None
            except Exception:
                # Import itself can initialize Windows COM and fail before the
                # loader call. Keep its diagnostics inside the same boundary.
                raise ChromeCookieImportError(ChromeImportFailure.DEPENDENCY_INIT) from None
            try:
                loaded = browser_cookie3.chrome(
                    cookie_file=str(cookie_file), key_file=str(key_file), domain_name=domain
                )
                result = CookieJar()
                for cookie in loaded:
                    # The dependency's substring filter is not a domain boundary.
                    if cookie.domain.lstrip(".").lower() == domain.lstrip(".").lower():
                        result.set_cookie(cookie)
                return result
            except Exception:
                # Dependency exceptions can contain paths, encrypted keys or SQL data.
                raise ChromeCookieImportError(ChromeImportFailure.LOADER) from None
