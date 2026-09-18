"""Encrypted, profile-scoped CookieJar persistence for the local v3 runtime."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from http.cookiejar import Cookie, CookieJar
from pathlib import Path
from typing import cast

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from ..exceptions import AuthenticationError
from .filesystem import FileSystem, atomic_write
from .interprocess_lock import InterProcessFileLock

_KEY_SERVICE = "image-downloader.cookie-key"
_FORMAT = 1
CookieKey = tuple[str, str, str]
CookieSnapshot = dict[CookieKey, dict[str, object]]


class CookieStore:
    """Keep encrypted cookies in a profile while the key remains in the OS keyring."""

    def __init__(self, filesystem: FileSystem, *, lock_timeout_seconds: float = 30.0) -> None:
        self.filesystem = filesystem
        self.relative = Path("cookies.enc")
        self.lock_relative = Path("cookies.lock")
        self.path = filesystem.path(self.relative)
        self.account = hashlib.sha256(str(self.path.resolve()).encode("utf-8")).hexdigest()
        self.lock_timeout_seconds = lock_timeout_seconds

    def load(self) -> CookieJar:
        with self._lock():
            return self._load_unlocked()

    def _load_unlocked(self) -> CookieJar:
        if not self.filesystem.exists(self.relative):
            return CookieJar()
        try:
            payload = self.filesystem.read_text(self.relative)
            key = self._key(create=False)
            if key is None:
                raise AuthenticationError("cookie encryption key is missing")
            jar = _deserialize(_decrypt(json.loads(payload), key).decode("utf-8"))
            jar.clear_expired_cookies()
            return jar
        except AuthenticationError:
            raise
        except (OSError, ValueError, TypeError, json.JSONDecodeError, InvalidTag) as exc:
            raise AuthenticationError("encrypted cookie file cannot be decrypted") from exc

    def save(self, jar: CookieJar) -> None:
        """Explicitly replace the complete persisted jar."""
        with self._lock():
            self._save_unlocked(jar)

    def _save_unlocked(self, jar: CookieJar) -> None:
        jar.clear_expired_cookies()
        try:
            payload = json.dumps(
                _encrypt(_serialize(jar).encode("utf-8"), self._key(create=True)), separators=(",", ":")
            )
            self.filesystem.write_bytes_atomic(self.relative, payload.encode("utf-8"))
        except AuthenticationError:
            raise
        except OSError as exc:
            raise AuthenticationError("could not persist encrypted cookie file") from exc

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
            source = json.loads(path.read_text(encoding="utf-8"))
            if source.get("kind") != "image-downloader-cookie-export" or source.get("kdf") != "scrypt":
                raise ValueError("unsupported cookie export")
            imported = _deserialize(_decrypt(source, _derive(passphrase, _unb64(str(source["salt"])))).decode("utf-8"))
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError, InvalidTag) as exc:
            raise AuthenticationError("cookie import cannot be decrypted") from exc
        with self._lock():
            try:
                target = self._load_unlocked()
            except AuthenticationError:
                # An explicit import is also the supported recovery path for a
                # damaged local cookie container.
                target = CookieJar()
            self._merge(target, imported)
            self._save_unlocked(target)

    def import_browser(self, domain: str) -> None:
        try:
            import browser_cookie3

            imported = browser_cookie3.load(domain_name=domain)
        except ImportError as exc:
            raise AuthenticationError("browser cookie import requires browser-cookie3") from exc
        with self._lock():
            target = self._load_unlocked()
            if self._merge(target, imported):
                self._save_unlocked(target)

    @staticmethod
    def snapshot(jar: CookieJar) -> CookieSnapshot:
        """Capture persisted cookie fields without retaining a mutable jar reference."""
        return {_cookie_key(cookie): _cookie_record(cookie) for cookie in jar}

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

    def _lock(self) -> InterProcessFileLock:
        self.filesystem.ensure_directory()
        # Validate an existing sidecar before opening it through the OS lock API.
        self.filesystem.exists(self.lock_relative)
        return InterProcessFileLock(
            self.filesystem.path(self.lock_relative), timeout_seconds=self.lock_timeout_seconds
        )

    def _key(self, *, create: bool) -> bytes | None:
        try:
            import keyring

            value = keyring.get_password(_KEY_SERVICE, self.account)
            if value:
                return _unb64(value)
            if not create:
                return None
            key = secrets.token_bytes(32)
            keyring.set_password(_KEY_SERVICE, self.account, _b64(key))
            return key
        except Exception as exc:
            raise AuthenticationError("could not access cookie encryption key in the OS credential store") from exc


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


def _decrypt(value: dict[str, object], key: bytes) -> bytes:
    if value.get("format") != _FORMAT or value.get("cipher") != "AES-256-GCM":
        raise ValueError("unsupported cookie format")
    return AESGCM(key).decrypt(_unb64(str(value["nonce"])), _unb64(str(value["ciphertext"])), None)


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


def _cookie_from_record(record: dict[str, object]) -> Cookie:
    return _cookie(
        str(record["name"]),
        str(record["value"]),
        str(record["domain"]),
        path=str(record.get("path", "/")),
        secure=bool(record.get("secure", False)),
        expires=cast(int | None, record.get("expires")),
        rest=cast(dict[str, object], record.get("rest", {})),
        domain_specified=bool(record.get("domain_specified", False)),
        domain_initial_dot=bool(record.get("domain_initial_dot", False)),
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
    value: str,
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
