"""Profile-owned credentials; the plugin API receives only selected reads."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from ..exceptions import SecretNotFoundError, VaultError
from ..storage.interprocess_lock import InterProcessFileLock
from .vault_crypto import SECRET_LIMIT, decode, encode
from .vault_files import private_directory

if TYPE_CHECKING:
    from .vault import CredentialVault


def reference_name(value: str) -> str:
    if not re.fullmatch(r"[A-Z0-9_]+", value):
        raise VaultError("input")
    return value


def plugin_name(value: str) -> str:
    from ..configuration.models import _valid_plugin_id

    if not _valid_plugin_id(value):
        raise VaultError("input")
    return value


def secret_value(value: object) -> str:
    if not isinstance(value, str):
        raise VaultError("input")
    try:
        if len(value.encode("utf-8")) > SECRET_LIMIT:
            raise VaultError("size")
    except UnicodeError:
        raise VaultError("input") from None
    return value


def validate_payload(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != {"format", "plugins", "smtp"}
        or type(value["format"]) is not int
        or value["format"] != 1
    ):
        raise VaultError("corrupt")
    if not isinstance(value["plugins"], dict) or not isinstance(value["smtp"], dict):
        raise VaultError("corrupt")
    for plugin, references in value["plugins"].items():
        plugin_name(plugin)
        if not isinstance(references, dict):
            raise VaultError("corrupt")
        for reference, secret in references.items():
            reference_name(reference)
            secret_value(secret)
    for reference, pair in value["smtp"].items():
        reference_name(reference)
        if not isinstance(pair, dict) or set(pair) != {"username", "password"}:
            raise VaultError("corrupt")
        secret_value(pair["username"])
        secret_value(pair["password"])
    return value


@dataclass(frozen=True, repr=False)
class SmtpCredential:
    username: str
    password: str

    def __repr__(self) -> str:
        return "SmtpCredential(<redacted>)"


class ProfileCredentialStore:
    def __init__(self, vault: CredentialVault, profile: str) -> None:
        self.vault, self.profile = vault, profile

    @contextmanager
    def _locked(self, *, write: bool = False) -> Iterator[None]:
        with self.vault.locked(write=write):
            relative = self.vault.relative(self.profile, "credentials")
            if not write and not self.vault._exists(relative):
                yield
                return
            if write:
                private_directory(self.vault.filesystem, relative.parent)
            lock = relative.with_suffix(".lock")
            self.vault._exists(lock)
            with InterProcessFileLock(
                self.vault.filesystem.path(lock), timeout_seconds=self.vault.lock_timeout_seconds
            ):
                yield

    def _load(self) -> dict[str, Any]:
        data = self.vault.read_locked(self.profile, "credentials")
        return {"format": 1, "plugins": {}, "smtp": {}} if data is None else validate_payload(decode(data))

    def get_plugin(self, plugin_id: str, reference: str) -> str:
        plugin_name(plugin_id)
        reference_name(reference)
        with self._locked():
            value = self._load()["plugins"].get(plugin_id, {}).get(reference)
        if value is None:
            raise SecretNotFoundError("required plugin secret is unavailable")
        return cast(str, value)

    def get_smtp(self, reference: str) -> SmtpCredential:
        reference_name(reference)
        with self._locked():
            value = self._load()["smtp"].get(reference)
        if value is None:
            raise SecretNotFoundError("required SMTP credential is unavailable")
        return SmtpCredential(value["username"], value["password"])

    def set_plugin(self, plugin_id: str, reference: str, value: str) -> None:
        plugin_name(plugin_id)
        reference_name(reference)
        secret_value(value)
        with self._locked(write=True):
            data = self._load()
            data["plugins"].setdefault(plugin_id, {})[reference] = value
            self.vault.write_locked(self.profile, "credentials", encode(data))

    def set_smtp(self, reference: str, username: str, password: str) -> None:
        reference_name(reference)
        secret_value(username)
        secret_value(password)
        with self._locked(write=True):
            data = self._load()
            data["smtp"][reference] = {"username": username, "password": password}
            self.vault.write_locked(self.profile, "credentials", encode(data))

    def list_entries(self, kind: str) -> list[dict[str, str]]:
        with self._locked():
            data = self._load()
        if kind == "plugin":
            return [
                {"kind": "plugin", "plugin_id": plugin, "reference": reference}
                for plugin, refs in sorted(data["plugins"].items())
                for reference in sorted(refs)
            ]
        if kind == "smtp":
            return [{"kind": "smtp", "reference": reference} for reference in sorted(data["smtp"])]
        raise VaultError("input")

    def delete(self, kind: str, reference: str, plugin_id: str | None = None) -> bool:
        reference_name(reference)
        if kind == "plugin":
            plugin_name(plugin_id or "")
        elif kind != "smtp":
            raise VaultError("input")
        with self._locked(write=True):
            data = self._load()
            entries = data["plugins"].get(plugin_id, {}) if kind == "plugin" else data["smtp"]
            if reference not in entries:
                return False
            del entries[reference]
            if kind == "plugin" and not entries:
                del data["plugins"][plugin_id]
            self.vault.write_locked(self.profile, "credentials", encode(data))
        return True
