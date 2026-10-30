"""Encrypted, profile-scoped CookieJar persistence for the local v3 runtime."""

from __future__ import annotations

import base64
import json
import secrets
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.cookiejar import Cookie, CookieJar
from pathlib import Path
from typing import cast

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .._numeric import _is_finite
from ..credentials.profile_store import ProfileCredentialStore
from ..credentials.vault_crypto import FILE_LIMIT
from ..credentials.vault_files import private_directory
from ..exceptions import AuthenticationError
from .filesystem import FileSystem, atomic_write
from .interprocess_lock import InterProcessFileLock
from .path_safety import existing_directory

_FORMAT = 1
CookieKey = tuple[str, str, str]
CookieSnapshot = dict[CookieKey, dict[str, object]]


class CookieStore:
    """Profile cookies protected by the shared vault master key."""

    def __init__(
        self, filesystem: FileSystem, *, store: ProfileCredentialStore, lock_timeout_seconds: float = 30.0
    ) -> None:
        self.filesystem = filesystem
        self.store = store
        expected = store.vault.filesystem.path(store.vault.relative(store.profile, "cookies")).parent
        if filesystem.root != expected:
            raise ValueError("CookieStore filesystem must match its profile store")
        self.relative = Path("cookies.v2.enc")
        self.lock_relative = Path("cookies.v2.lock")
        self.path = filesystem.path(self.relative)
        self.lock_timeout_seconds = lock_timeout_seconds
        self._legacy_warned = False

    def load(self) -> CookieJar:
        if not self.filesystem.exists(self.relative):
            self.store.vault.check_pending()
            if not self._legacy_warned and self.filesystem.exists(Path("cookies.enc")):
                sys.stderr.write("warning: legacy cookies are unused; import browser cookies or a cookie export\n")
                self._legacy_warned = True
            return CookieJar()
        with self._lock(write=False):
            return self._load_unlocked()

    def _load_unlocked(self) -> CookieJar:
        try:
            payload = self.store.vault.read_locked(self.store.profile, "cookies")
            if payload is None:
                return CookieJar()
            jar = _deserialize(payload.decode("utf-8"))
            jar.clear_expired_cookies()
            return jar
        except AuthenticationError:
            raise
        except (OSError, ValueError, TypeError, RecursionError, json.JSONDecodeError, InvalidTag):
            raise AuthenticationError("encrypted cookie file cannot be decrypted") from None

    def save(self, jar: CookieJar) -> None:
        """Explicitly replace the complete persisted jar."""
        with self._lock():
            self._save_unlocked(jar)

    def _save_unlocked(self, jar: CookieJar) -> None:
        jar.clear_expired_cookies()
        try:
            self.store.vault.write_locked(self.store.profile, "cookies", _serialize(jar).encode("utf-8"))
        except AuthenticationError:
            raise
        except OSError:
            raise AuthenticationError("could not persist encrypted cookie file") from None

    def export_file(self, path: Path, passphrase: str) -> None:
        if not passphrase:
            raise AuthenticationError("cookie export passphrase is required")
        salt = secrets.token_bytes(16)
        container = _encrypt(_serialize(self.load()).encode("utf-8"), _derive(passphrase, salt))
        container.update({"kind": "image-downloader-cookie-export", "kdf": "scrypt", "salt": _b64(salt)})
        try:
            atomic_write(path, json.dumps(container, separators=(",", ":")).encode("utf-8"))
        except OSError as exc:
            raise AuthenticationError("could not write cookie export") from exc

    def import_file(self, path: Path, passphrase: str) -> None:
        if not passphrase:
            raise AuthenticationError("cookie import passphrase is required")
        try:
            source = json.loads(FileSystem(path.parent).read_bytes_bounded(Path(path.name), FILE_LIMIT))
            salt = _export_salt(source)
            imported = _deserialize(_decrypt(source, _derive(passphrase, salt)).decode("utf-8"))
        except (OSError, ValueError, TypeError, RecursionError, json.JSONDecodeError, InvalidTag):
            raise AuthenticationError("cookie import cannot be decrypted") from None
        with self._lock():
            target = self._load_unlocked()
            self._merge(target, imported)
            self._save_unlocked(target)

    def import_browser(self, domain: str, *, chrome_user_data_dir: Path | None = None) -> None:
        if chrome_user_data_dir is not None:
            from .browser_cookies import import_chrome

            imported = import_chrome(domain, chrome_user_data_dir)
        else:
            imported = self._discover_browser(domain)
        imported = _normalize_browser_cookies(imported)
        with self._lock():
            target = self._load_unlocked()
            if self._merge(target, imported):
                self._save_unlocked(target)

    @staticmethod
    def _discover_browser(domain: str) -> CookieJar:
        try:
            import browser_cookie3

            return cast(CookieJar, browser_cookie3.load(domain_name=domain))
        except ImportError as exc:
            raise AuthenticationError("browser cookie import requires browser-cookie3") from exc

    @staticmethod
    def snapshot(jar: CookieJar) -> CookieSnapshot:
        """Capture persisted cookie fields without retaining a mutable jar reference."""
        return {_cookie_key(cookie): _cookie_record(cookie) for cookie in jar}

    @staticmethod
    def clone_jar(jar: CookieJar) -> CookieJar:
        """Return a detached, non-expired copy suitable for an ephemeral operation."""
        clone = _deserialize(_serialize(jar))
        clone.clear_expired_cookies()
        return clone

    def persist_delta(self, baseline: CookieSnapshot, current: CookieJar) -> None:
        """Apply only this service's changes to the latest persisted jar."""
        current_snapshot = self.snapshot(current)
        now = time.time()
        changes: CookieSnapshot = {}
        deletions: set[CookieKey] = set()
        for key in baseline.keys() | current_snapshot.keys():
            before = baseline.get(key)
            after = current_snapshot.get(key)
            if before == after:
                continue
            if after is None or _expired(after, now):
                # Natural expiry is not an explicit deletion of another process's update.
                if before is not None and (after is not None or not _expired(before, now)):
                    deletions.add(key)
            else:
                changes[key] = after
        if not changes and not deletions:
            return
        with self._lock():
            target = self._load_unlocked()
            existing = self.snapshot(target)
            for key in deletions:
                if key in existing:
                    target.clear(*key)
            for key, record in changes.items():
                target.set_cookie(_resolved_cookie(record, existing.get(key)))
            self._save_unlocked(target)

    def _merge(self, target: CookieJar, imported: CookieJar) -> bool:
        existing = self.snapshot(target)
        changed = False
        now = time.time()
        for cookie in imported:
            record = _cookie_record(cookie)
            if _expired(record, now):
                continue
            key = _cookie_key(cookie)
            resolved = _resolved_cookie(record, existing.get(key))
            if existing.get(key) != _cookie_record(resolved):
                target.set_cookie(resolved)
                changed = True
        return changed

    @contextmanager
    def _lock(self, *, write: bool = True) -> Iterator[None]:
        with self.store.vault.locked(write=write):
            if write:
                private_directory(self.filesystem)
            else:
                existing_directory(self.filesystem.root, "cookie root", required=True)
            self.filesystem.exists(self.lock_relative)
            with InterProcessFileLock(
                self.filesystem.path(self.lock_relative), timeout_seconds=self.lock_timeout_seconds
            ):
                yield


def _normalize_browser_cookies(imported: CookieJar) -> CookieJar:
    """Adapt the browser library's SQLite flags to the strict storage schema."""
    normalized = CookieJar()
    try:
        for cookie in imported:
            record = _cookie_record(cookie)
            # browser-cookie3 passes SQLite's 0/1 is_secure into Cookie without
            # coercion. Accept exactly those integers at this import boundary;
            # persisted/exported records still require actual bool values.
            secure = record["secure"]
            if type(secure) is int and secure in (0, 1):
                record["secure"] = bool(secure)
            normalized.set_cookie(_cookie_from_record(record))
    except (ValueError, TypeError, AttributeError, OverflowError, RecursionError):
        raise AuthenticationError("browser cookie data is invalid") from None
    return normalized


def _encrypt(value: bytes, key: bytes | None) -> dict[str, object]:
    if key is None:
        raise AuthenticationError("cookie encryption key is missing")
    nonce = secrets.token_bytes(12)
    return {
        "format": _FORMAT,
        "cipher": "AES-256-GCM",
        "nonce": _b64(nonce),
        "ciphertext": _b64(AESGCM(key).encrypt(nonce, value, None)),
    }


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("cookie data must be an object")
    return value


def _binary_field(value: dict[str, object], name: str) -> bytes:
    encoded = value.get(name)
    if not isinstance(encoded, str):
        raise ValueError("invalid cookie binary field")
    return _unb64(encoded)


def _export_salt(value: object) -> bytes:
    container = _object(value)
    if container.get("kind") != "image-downloader-cookie-export" or container.get("kdf") != "scrypt":
        raise ValueError("unsupported cookie export")
    salt = _binary_field(container, "salt")
    if len(salt) != 16:
        raise ValueError("invalid cookie export salt")
    return salt


def _decrypt(value: object, key: bytes) -> bytes:
    container = _object(value)
    if type(container.get("format")) is not int or container.get("format") != _FORMAT:
        raise ValueError("unsupported cookie format")
    if container.get("cipher") != "AES-256-GCM":
        raise ValueError("unsupported cookie cipher")
    nonce = _binary_field(container, "nonce")
    ciphertext = _binary_field(container, "ciphertext")
    if len(nonce) != 12 or len(ciphertext) < 16:
        raise ValueError("invalid cookie encrypted data")
    return AESGCM(key).decrypt(nonce, ciphertext, None)


def _derive(passphrase: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2**14, r=8, p=1).derive(passphrase.encode("utf-8"))


def _serialize(jar: CookieJar) -> str:
    now = time.time()
    return json.dumps(
        [_cookie_record(cookie) for cookie in jar if not _expired(_cookie_record(cookie), now)],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _deserialize(payload: str) -> CookieJar:
    data = json.loads(payload)
    if not isinstance(data, list):
        raise ValueError("cookie payload must be a list")
    jar = CookieJar()
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("invalid cookie item")
        jar.set_cookie(_cookie_from_record(item))
    return jar


def _cookie_key(cookie: Cookie) -> CookieKey:
    return (cookie.domain, cookie.path, cookie.name)


def _cookie_record(cookie: Cookie) -> dict[str, object]:
    return {
        "name": cookie.name,
        "value": cookie.value,
        "domain": cookie.domain,
        "domain_specified": cookie.domain_specified,
        "domain_initial_dot": cookie.domain_initial_dot,
        "path": cookie.path,
        "secure": cookie.secure,
        "expires": cookie.expires,
        "rest": dict(getattr(cookie, "_rest", {})),
    }


def _string_field(record: dict[str, object], name: str, default: str | None = None) -> str:
    value = record.get(name, default)
    if not isinstance(value, str):
        raise ValueError("invalid cookie string field")
    value.encode("utf-8")
    return value


def _bool_field(record: dict[str, object], name: str) -> bool:
    value = record.get(name, False)
    if not isinstance(value, bool):
        raise ValueError("invalid cookie boolean field")
    return value


def _is_json_value(value: object) -> bool:
    pending = [(value, False)]
    ancestors: set[int] = set()
    while pending:
        item, leaving = pending.pop()
        if leaving:
            ancestors.remove(id(item))
        elif isinstance(item, (dict, list)):
            if id(item) in ancestors:
                return False
            if isinstance(item, dict) and any(not isinstance(key, str) for key in item):
                return False
            if isinstance(item, dict):
                for key in item:
                    key.encode("utf-8")
            ancestors.add(id(item))
            pending.append((item, True))
            pending.extend((child, False) for child in (item.values() if isinstance(item, dict) else item))
        elif isinstance(item, str):
            item.encode("utf-8")
        elif item is None or isinstance(item, (bool, int)):
            continue
        elif not isinstance(item, float) or not _is_finite(item):
            return False
    return True


def _cookie_from_record(record: dict[str, object]) -> Cookie:
    value = record.get("value")
    if "value" not in record or (value is not None and not isinstance(value, str)):
        raise ValueError("invalid cookie value")
    if value is not None:
        value.encode("utf-8")
    expires = record.get("expires")
    if expires is not None and (type(expires) is not int or not _is_finite(expires)):
        raise ValueError("invalid cookie expiry")
    rest = record.get("rest", {})
    if not isinstance(rest, dict) or not _is_json_value(rest):
        raise ValueError("invalid cookie attributes")
    return _cookie(
        _string_field(record, "name"),
        value,
        _string_field(record, "domain"),
        path=_string_field(record, "path", "/"),
        secure=_bool_field(record, "secure"),
        expires=expires,
        rest=rest,
        domain_specified=_bool_field(record, "domain_specified"),
        domain_initial_dot=_bool_field(record, "domain_initial_dot"),
    )


def _expired(record: dict[str, object], now: float) -> bool:
    expires = record.get("expires")
    return expires is not None and cast(int, expires) <= now


def _resolved_cookie(incoming: dict[str, object], latest: dict[str, object] | None) -> Cookie:
    record = dict(incoming)
    if latest is not None and incoming["value"] == latest["value"]:
        old_expiry = cast(int | None, latest.get("expires"))
        new_expiry = cast(int | None, incoming.get("expires"))
        if old_expiry is not None and (new_expiry is None or old_expiry > new_expiry):
            record["expires"] = old_expiry
    return _cookie_from_record(record)


def _cookie(
    name: str,
    value: str | None,
    domain: str,
    *,
    path: str = "/",
    secure: bool = False,
    expires: int | None = None,
    rest: dict[str, object] | None = None,
    domain_specified: bool = False,
    domain_initial_dot: bool = False,
) -> Cookie:
    return Cookie(
        0,
        name,
        value,
        None,
        False,
        domain,
        domain_specified,
        domain_initial_dot,
        path,
        True,
        secure,
        expires,
        expires is None,
        None,
        None,
        cast(dict[str, str], rest or {}),
        False,
    )


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"), validate=True)
