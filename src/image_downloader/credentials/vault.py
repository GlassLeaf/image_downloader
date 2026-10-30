"""Data-root scoped vault, durable initialization, backup and key rotation."""

from __future__ import annotations

import hashlib
import re
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from ..exceptions import VaultError
from ..storage.filesystem import FileSystem
from ..storage.interprocess_lock import InterProcessFileLock
from ..storage.path_safety import existing_directory, existing_regular_file
from .keyring import MasterKeyStore
from .vault_crypto import (
    BACKUP_LIMIT,
    FILE_LIMIT,
    b64,
    backup_key,
    binary,
    check_payload_size,
    context,
    decode,
    encode,
    envelope,
    open_envelope,
    seal,
    unseal,
)
from .vault_files import private_directory, restrict, write_private

if TYPE_CHECKING:
    from ..configuration.models import AppConfig
    from .profile_store import ProfileCredentialStore

_META = Path("security/vault.json")
_JOURNAL = Path("security/rotation.enc")
_METADATA_LIMIT = 64 * 1024
_BACKUP_WIRE_LIMIT = (BACKUP_LIMIT + 2) // 3 * 4 + 4096
_BACKUP_SCOPE = {"format": 1, "kind": "image-downloader-vault-backup", "kdf": "scrypt-17-8-1"}
_LOCATIONS = {"credentials": Path("credentials/credentials.enc"), "cookies": Path("cookie/cookies.v2.enc")}


def identifier(value: object) -> str:
    if not isinstance(value, str):
        raise VaultError("corrupt")
    try:
        if UUID(value).hex != value:
            raise ValueError
    except ValueError:
        raise VaultError("corrupt") from None
    return value


def profile_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise VaultError("input")
    return value


class CredentialVault:
    """All operations share the root lock; no network calls occur under it."""

    def __init__(
        self, data_root: Path, *, keys: MasterKeyStore | None = None, lock_timeout_seconds: float = 30
    ) -> None:
        self.filesystem = FileSystem(existing_directory(data_root, "credential data root"))
        self.keys = keys if keys is not None else MasterKeyStore()
        self.lock_timeout_seconds = lock_timeout_seconds

    @classmethod
    def from_config(cls, config: AppConfig) -> CredentialVault:
        from ..configuration.paths import resolve_paths

        return cls(resolve_paths(config)["profile"].parent.parent)

    def profile(self, name: str) -> ProfileCredentialStore:
        from .profile_store import ProfileCredentialStore

        return ProfileCredentialStore(self, profile_name(name))

    @contextmanager
    def locked(self, *, write: bool = False) -> Iterator[None]:
        fs = self.filesystem
        try:
            existing_directory(fs.root, "credential data root")
            security = fs.path(Path("security"))
            if not security.exists() and not write:
                # Validate even absent roots before yielding; no directories on read.
                existing_directory(security, "vault security root")
                yield
                return
            if write:
                private_directory(fs, Path("security"))
            else:
                existing_directory(security, "vault security root", required=True)
            fs.exists(Path("security/vault.lock"))
            with InterProcessFileLock(fs.path(Path("security/vault.lock")), timeout_seconds=self.lock_timeout_seconds):
                yield
        except OSError:
            raise VaultError("access") from None

    def _read_json(self, relative: Path, limit: int = FILE_LIMIT) -> Any:
        path = existing_regular_file(self.filesystem.path(relative), "vault file", required=True)
        assert path is not None
        if path.stat().st_size > limit:
            raise VaultError("size")
        return decode(self.filesystem.read_bytes_bounded(relative, limit))

    def _exists(self, relative: Path) -> bool:
        path = self.filesystem.path(relative)
        return existing_regular_file(path, "vault file", required=False) is not None

    def _write_json(self, relative: Path, value: object, limit: int = FILE_LIMIT) -> None:
        write_private(self.filesystem, relative, encode(value), limit)

    def _meta(self) -> dict[str, Any] | None:
        if not self._exists(_META):
            return None
        meta = self._read_json(_META, _METADATA_LIMIT)
        if not isinstance(meta, dict) or type(meta.get("format")) is not int or meta["format"] != 1:
            raise VaultError("format")
        if set(meta) not in (
            {"format", "vault_id", "key_id", "state", "marker"},
            {"format", "vault_id", "key_id", "state", "marker", "restore_hash"},
        ):
            raise VaultError("corrupt")
        identifier(meta["vault_id"])
        identifier(meta["key_id"])
        if not isinstance(meta["state"], str) or meta["state"] not in {"initializing", "ready", "restoring"}:
            raise VaultError("corrupt")
        if meta["state"] == "ready" and meta["marker"] is None:
            raise VaultError("corrupt")
        return meta

    def _key(self, meta: dict[str, Any], key_id: str | None = None) -> bytes:
        key = self.keys.get(meta["vault_id"], key_id or meta["key_id"])
        if key is None:
            raise VaultError("key_missing")
        return key

    @staticmethod
    def _marker_aad(meta: dict[str, Any]) -> bytes:
        return encode({k: v for k, v in meta.items() if k != "marker"})

    def _verify(self, meta: dict[str, Any], key: bytes) -> None:
        if unseal(meta["marker"], key, self._marker_aad(meta)) != b"image-downloader-vault":
            raise VaultError("corrupt")

    def _publish_meta(self, meta: dict[str, Any], key: bytes) -> None:
        meta["marker"] = seal(b"image-downloader-vault", key, self._marker_aad(meta))
        self._write_json(_META, meta, _METADATA_LIMIT)

    def _managed(self) -> list[tuple[str, str]]:
        fs = self.filesystem
        profiles = fs.path(Path("profiles"))
        if not profiles.exists():
            existing_directory(profiles, "vault profiles")
            return []
        existing_directory(profiles, "vault profiles", required=True)
        result = []
        for child in fs.child_paths(Path("profiles")):
            path = fs.path(child)
            if not path.is_dir():
                continue
            existing_directory(path, "vault profile", required=True)
            name = profile_name(child.name)
            for purpose in _LOCATIONS:
                if self._exists(self.relative(name, purpose)):
                    result.append((name, purpose))
        return sorted(result)

    @staticmethod
    def relative(profile: str, purpose: str) -> Path:
        if purpose not in _LOCATIONS:
            raise VaultError("input")
        return Path("profiles") / profile_name(profile) / _LOCATIONS[purpose]

    def _initialize(self) -> tuple[dict[str, Any], bytes]:
        meta = self._meta()
        if meta is None:
            if self._managed() or self._exists(_JOURNAL):
                raise VaultError("corrupt")
            meta = {
                "format": 1,
                "vault_id": uuid4().hex,
                "key_id": uuid4().hex,
                "state": "initializing",
                "marker": None,
            }
            self._write_json(_META, meta, _METADATA_LIMIT)
        if meta["state"] != "initializing":
            return self._ready()
        if self._managed():
            raise VaultError("corrupt")
        key = self.keys.get(meta["vault_id"], meta["key_id"])
        if key is None:
            key = secrets.token_bytes(32)
            self.keys.create(meta["vault_id"], meta["key_id"], key)
        meta["state"] = "ready"
        self._publish_meta(meta, key)
        return meta, key

    def _ready(self, *, create: bool = False) -> tuple[dict[str, Any], bytes]:
        meta = self._meta()
        if meta is None or meta["state"] == "initializing":
            if create:
                return self._initialize()
            raise VaultError("missing" if meta is None else "pending")
        key = self._key(meta)
        self._verify(meta, key)
        if meta["state"] != "ready":
            raise VaultError("pending")
        if self._exists(_JOURNAL):
            journal = self._rotation(meta)
            if journal["phase"] != "committed" or meta["key_id"] != journal["new"]:
                raise VaultError("rotation")
        return meta, key

    def initialize(self) -> None:
        with self.locked(write=True):
            self._initialize()

    def status(self) -> dict[str, object]:
        with self.locked():
            meta = self._meta()
            if meta is None:
                return {"state": "uninitialized"}
            if meta["marker"] is not None:
                self._verify(meta, self._key(meta))
            state = meta["state"]
            if self._exists(_JOURNAL):
                journal = self._rotation(meta)
                retired = journal["phase"] == "committed" and meta["key_id"] == journal["new"]
                state = "retirement_pending" if retired else "rotation_pending"
            return {"state": state, "vault_id": meta["vault_id"], "key_id": meta["key_id"]}

    def check_pending(self) -> None:
        with self.locked():
            meta = self._meta()
            if meta is not None:
                self._ready()

    def read_locked(self, profile: str, purpose: str) -> bytes | None:
        path = self.relative(profile, purpose)
        if not self._exists(path):
            meta = self._meta()
            if meta is not None:
                self._ready()
            return None
        meta, key = self._ready()
        scope = context(meta["vault_id"], profile, purpose)
        value = self._read_json(path)
        return open_envelope(value, key, meta["key_id"], scope)[0]

    def _put(self, profile: str, purpose: str, data: bytes, meta: dict[str, Any], key: bytes) -> None:
        if len(data) > FILE_LIMIT:
            raise VaultError("size")
        self._write_json(
            self.relative(profile, purpose),
            envelope(data, key, meta["key_id"], context(meta["vault_id"], profile, purpose)),
        )

    def write_locked(self, profile: str, purpose: str, data: bytes) -> None:
        check_payload_size(len(data), context("0" * 32, profile, purpose))
        meta, key = self._ready(create=True)
        path = self.relative(profile, purpose)
        if self._exists(path):
            previous = open_envelope(
                self._read_json(path), key, meta["key_id"], context(meta["vault_id"], profile, purpose)
            )[0]
            self._validated(profile, purpose, previous)
        self._put(profile, purpose, data, meta, key)

    def _validated(self, profile: str, purpose: str, data: bytes) -> None:
        if purpose == "cookies":
            from ..storage.cookies import _deserialize

            try:
                _deserialize(data.decode("utf-8"))
            except (ValueError, TypeError, UnicodeError, RecursionError):
                raise VaultError("corrupt") from None
        else:
            from .profile_store import validate_payload

            validate_payload(decode(data))

    def backup(self, destination: Path, passphrase: str) -> None:
        try:
            relative = destination.relative_to(self.filesystem.root)
        except ValueError:
            relative = None
        if relative is not None:
            managed = (
                len(relative.parts) == 4
                and Path(relative.parts[0]) == Path("profiles")
                and Path(*relative.parts[2:]) in _LOCATIONS.values()
            )
            if managed or relative in {_META, _JOURNAL, Path("security/vault.lock")}:
                raise VaultError("input")
        with self.locked():
            self._ready()
            files = []
            for profile, purpose in self._managed():
                data = self.read_locked(profile, purpose)
                assert data is not None
                self._validated(profile, purpose, data)
                files.append({"profile": profile, "purpose": purpose, "data": b64(data)})
                if sum(len(item["data"]) for item in files) > BACKUP_LIMIT:
                    raise VaultError("size")
            plaintext = encode({"format": 1, "files": files})
            if len(plaintext) > BACKUP_LIMIT:
                raise VaultError("size")
        salt = secrets.token_bytes(16)
        header = {**_BACKUP_SCOPE, "salt": b64(salt)}
        payload = {**header, "data": seal(plaintext, backup_key(passphrase, salt), encode(header))}
        target = FileSystem(existing_directory(destination.parent, "backup parent", required=True))
        if target.exists(Path(destination.name)):
            raise VaultError("exists")
        # Do not change permissions on the user's chosen backup parent directory.
        target.write_bytes_atomic_new(Path(destination.name), encode(payload))
        restrict(target.path(Path(destination.name)), directory=False)

    def _backup_contents(self, source: Path, passphrase: str) -> tuple[list[dict[str, str]], str]:
        fs = FileSystem(existing_directory(source.parent, "backup parent", required=True))
        checked = existing_regular_file(source, "vault backup", required=True)
        assert checked is not None
        if checked.stat().st_size > _BACKUP_WIRE_LIMIT:
            raise VaultError("size")
        raw = fs.read_bytes_bounded(Path(source.name), _BACKUP_WIRE_LIMIT)
        container = decode(raw)
        if not isinstance(container, dict) or set(container) != {*_BACKUP_SCOPE, "salt", "data"}:
            raise VaultError("format")
        if type(container["format"]) is not int or any(container.get(k) != v for k, v in _BACKUP_SCOPE.items()):
            raise VaultError("format")
        header = {k: v for k, v in container.items() if k != "data"}
        plain = unseal(container["data"], backup_key(passphrase, binary(container["salt"], 16)), encode(header))
        if len(plain) > BACKUP_LIMIT:
            raise VaultError("size")
        result = decode(plain)
        if (
            not isinstance(result, dict)
            or set(result) != {"format", "files"}
            or type(result["format"]) is not int
            or result["format"] != 1
        ):
            raise VaultError("format")
        files = result["files"]
        if not isinstance(files, list):
            raise VaultError("corrupt")
        seen = set()
        for item in files:
            if not isinstance(item, dict) or set(item) != {"profile", "purpose", "data"}:
                raise VaultError("corrupt")
            if (
                not isinstance(item["profile"], str)
                or not isinstance(item["purpose"], str)
                or item["purpose"] not in _LOCATIONS
            ):
                raise VaultError("corrupt")
            pair = (profile_name(item["profile"]), item["purpose"])
            if pair in seen:
                raise VaultError("corrupt")
            seen.add(pair)
            data = binary(item["data"])
            if len(data) > FILE_LIMIT:
                raise VaultError("size")
            check_payload_size(len(data), context("0" * 32, *pair))
            self._validated(*pair, data)
        return files, hashlib.sha256(raw).hexdigest()

    def restore(self, source: Path, passphrase: str) -> None:
        files, digest = self._backup_contents(source, passphrase)
        with self.locked(write=True):
            meta = self._meta()
            if meta is None:
                children = self.filesystem.child_paths()
                security = self.filesystem.child_paths(Path("security"))
                if set(children) != {Path("security")} or set(security) - {Path("security/vault.lock")}:
                    raise VaultError("destination")
                meta = {
                    "format": 1,
                    "vault_id": uuid4().hex,
                    "key_id": uuid4().hex,
                    "state": "restoring",
                    "marker": None,
                    "restore_hash": digest,
                }
                self._write_json(_META, meta, _METADATA_LIMIT)
            if meta["state"] != "restoring" or meta.get("restore_hash") != digest:
                raise VaultError("destination")
            key = self.keys.get(meta["vault_id"], meta["key_id"])
            if key is None:
                if self._managed() or meta["marker"] is not None:
                    raise VaultError("key_missing")
                key = secrets.token_bytes(32)
                self.keys.create(meta["vault_id"], meta["key_id"], key)
            if meta["marker"] is not None:
                self._verify(meta, key)
            else:
                self._publish_meta(meta, key)
            expected = {(item["profile"], item["purpose"]) for item in files}
            if set(self._managed()) - expected:
                raise VaultError("corrupt")
            for item in files:
                data = binary(item["data"])
                self._put(item["profile"], item["purpose"], data, meta, key)
                value = self._read_json(self.relative(item["profile"], item["purpose"]))
                scope = context(meta["vault_id"], item["profile"], item["purpose"])
                if open_envelope(value, key, meta["key_id"], scope)[0] != data:
                    raise VaultError("corrupt")
            meta.pop("restore_hash")
            meta["state"] = "ready"
            self._publish_meta(meta, key)

    def _rotation(self, meta: dict[str, Any]) -> dict[str, Any]:
        value = self._read_json(_JOURNAL)
        if not isinstance(value, dict):
            raise VaultError("corrupt")
        key_id = identifier(value.get("key_id"))
        scope = context(meta["vault_id"], "", "rotation")
        data = decode(open_envelope(value, self._key(meta, key_id), key_id, scope)[0])
        if not isinstance(data, dict) or set(data) != {"old", "new", "phase", "files"}:
            raise VaultError("corrupt")
        old, new = identifier(data["old"]), identifier(data["new"])
        if old == new or meta["key_id"] not in {old, new}:
            raise VaultError("corrupt")
        if not isinstance(data["phase"], str) or data["phase"] not in {"prepared", "rewrapping", "committed"}:
            raise VaultError("corrupt")
        expected_key = new if data["phase"] == "committed" else old
        if key_id != expected_key or not isinstance(data["files"], list):
            raise VaultError("corrupt")
        for item in data["files"]:
            if not isinstance(item, list) or len(item) != 2 or not all(isinstance(s, str) for s in item):
                raise VaultError("corrupt")
            self.relative(*item)
        return data

    def _write_rotation(self, meta: dict[str, Any], journal: dict[str, Any]) -> None:
        key_id = journal["new"] if journal["phase"] == "committed" else journal["old"]
        self._write_json(
            _JOURNAL,
            envelope(encode(journal), self._key(meta, key_id), key_id, context(meta["vault_id"], "", "rotation")),
        )

    def rotate_key(self, *, resume: bool = False) -> None:
        with self.locked(write=True):
            meta = self._meta()
            if meta is None:
                raise VaultError("missing")
            self._verify(meta, self._key(meta))
            if self._exists(_JOURNAL):
                if not resume:
                    raise VaultError("rotation")
                journal = self._rotation(meta)
            else:
                if meta["state"] != "ready":
                    raise VaultError("pending")
                if resume:
                    raise VaultError("no_rotation")
                files = self._managed()
                for profile, purpose in files:
                    data = self.read_locked(profile, purpose)
                    assert data is not None
                    self._validated(profile, purpose, data)
                journal = {
                    "old": meta["key_id"],
                    "new": uuid4().hex,
                    "phase": "prepared",
                    "files": [list(item) for item in files],
                }
                self._write_rotation(meta, journal)
            self._finish_rotation(meta, journal)

    def _finish_rotation(self, meta: dict[str, Any], journal: dict[str, Any]) -> None:
        new_id = journal["new"]
        new_key = self.keys.get(meta["vault_id"], new_id)
        if new_key is None:
            if journal["phase"] != "prepared":
                raise VaultError("key_missing")
            new_key = secrets.token_bytes(32)
            self.keys.create(meta["vault_id"], new_id, new_key)
        if journal["phase"] != "committed":
            if self._managed() != sorted(tuple(item) for item in journal["files"]):
                raise VaultError("corrupt")
            journal["phase"] = "rewrapping"
            self._write_rotation(meta, journal)
            for profile, purpose in journal["files"]:
                self._rewrap(meta, profile, purpose, journal, new_key)
            journal["phase"] = "committed"
            self._write_rotation(meta, journal)
        # The committed journal is readable with the new key, even after retirement.
        if set(tuple(item) for item in journal["files"]) - set(self._managed()):
            raise VaultError("corrupt")
        for profile, purpose in self._managed():
            value = self._read_json(self.relative(profile, purpose))
            scope = context(meta["vault_id"], profile, purpose)
            data = open_envelope(value, new_key, new_id, scope)[0]
            self._validated(profile, purpose, data)
        meta["key_id"] = new_id
        self._publish_meta(meta, new_key)
        self.keys.delete(meta["vault_id"], journal["old"])
        self.filesystem.path(_JOURNAL).unlink()

    def _rewrap(
        self, meta: dict[str, Any], profile: str, purpose: str, journal: dict[str, Any], new_key: bytes
    ) -> None:
        path = self.relative(profile, purpose)
        value = self._read_json(path)
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("key_id"), str)
            or value["key_id"] not in {journal["old"], journal["new"]}
        ):
            raise VaultError("corrupt")
        key_id = value["key_id"]
        scope = context(meta["vault_id"], profile, purpose)
        data, data_key = open_envelope(value, self._key(meta, key_id), key_id, scope)
        self._validated(profile, purpose, data)
        if key_id == journal["old"]:
            value["key_id"] = journal["new"]
            value["wrapped_key"] = seal(data_key, new_key, encode({**scope, "key_id": journal["new"]}))
            self._write_json(path, value)
