"""Authenticated, scoped envelopes. No keyring or filesystem access."""

from __future__ import annotations

import base64
import json
import secrets
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from ..exceptions import VaultError

SECRET_LIMIT = 1024 * 1024
FILE_LIMIT = 64 * 1024 * 1024
BACKUP_LIMIT = 256 * 1024 * 1024


def encode(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def decode(value: bytes) -> Any:
    try:
        return json.loads(value)
    except (ValueError, UnicodeError, RecursionError):
        raise VaultError("corrupt") from None


def b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def binary(value: object, size: int | None = None) -> bytes:
    try:
        if not isinstance(value, str):
            raise ValueError
        result = base64.b64decode(value, validate=True)
        if size is not None and len(result) != size:
            raise ValueError
        return result
    except (ValueError, TypeError):
        raise VaultError("corrupt") from None


def seal(data: bytes, key: bytes, aad: bytes) -> dict[str, str]:
    nonce = secrets.token_bytes(12)
    return {"nonce": b64(nonce), "ciphertext": b64(AESGCM(key).encrypt(nonce, data, aad))}


def unseal(value: object, key: bytes, aad: bytes) -> bytes:
    try:
        if not isinstance(value, dict) or set(value) != {"nonce", "ciphertext"}:
            raise VaultError("corrupt")
        return AESGCM(key).decrypt(binary(value["nonce"], 12), binary(value["ciphertext"]), aad)
    except (InvalidTag, ValueError, TypeError, OverflowError):
        raise VaultError("corrupt") from None


def context(vault_id: str, profile: str, purpose: str) -> dict[str, object]:
    return {"format": 2, "vault_id": vault_id, "profile": profile, "purpose": purpose}


def envelope(data: bytes, key: bytes, key_id: str, scope: dict[str, object]) -> dict[str, object]:
    data_key = secrets.token_bytes(32)
    return {
        **scope,
        "key_id": key_id,
        "wrapped_key": seal(data_key, key, encode({**scope, "key_id": key_id})),
        "data": seal(data, data_key, encode(scope)),
    }


def check_payload_size(size: int, scope: dict[str, object]) -> None:
    # AES-GCM appends a 16-byte tag. UUIDs and nonces have fixed wire sizes.
    header = {
        **scope,
        "key_id": "0" * 32,
        "wrapped_key": {"nonce": "0" * 16, "ciphertext": "0" * 64},
        "data": {"nonce": "0" * 16, "ciphertext": ""},
    }
    if len(encode(header)) + ((size + 16 + 2) // 3) * 4 > FILE_LIMIT:
        raise VaultError("size")


def open_envelope(value: object, key: bytes, key_id: str, scope: dict[str, object]) -> tuple[bytes, bytes]:
    if not isinstance(value, dict) or type(value.get("format")) is not int or value["format"] != 2:
        raise VaultError("format")
    if set(value) != {*scope, "key_id", "wrapped_key", "data"}:
        raise VaultError("corrupt")
    if any(value.get(name) != item for name, item in scope.items()) or value["key_id"] != key_id:
        raise VaultError("scope")
    data_key = unseal(value["wrapped_key"], key, encode({**scope, "key_id": key_id}))
    if len(data_key) != 32:
        raise VaultError("corrupt")
    return unseal(value["data"], data_key, encode(scope)), data_key


def backup_key(passphrase: str, salt: bytes) -> bytes:
    try:
        data = passphrase.encode("utf-8")
    except (AttributeError, UnicodeError):
        raise VaultError("input") from None
    if not data or len(data) > SECRET_LIMIT:
        raise VaultError("input")
    return Scrypt(salt=salt, length=32, n=2**17, r=8, p=1).derive(data)
