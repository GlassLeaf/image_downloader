"""OS storage for immutable vault master keys only."""

from __future__ import annotations

from typing import Any, cast

from ..exceptions import VaultError
from .vault_crypto import b64, binary

_BACKENDS = frozenset(
    {
        ("keyring.backends.Windows", "WinVaultKeyring"),
        ("keyring.backends.macOS", "Keyring"),
        ("keyring.backends.SecretService", "Keyring"),
        ("keyring.backends.kwallet", "DBusKeyring"),
        ("keyring.backends.kwallet", "DBusKeyringKWallet4"),
    }
)


class MasterKeyStore:
    """Never overwrite a key or look up legacy application passwords."""

    @staticmethod
    def _backend() -> Any:
        try:
            import keyring

            backend = cast(Any, keyring.get_keyring())
            if (type(backend).__module__, type(backend).__name__) not in _BACKENDS:
                raise VaultError("backend")
            if type(backend).__module__ == "keyring.backends.Windows":
                backend.persist = "local machine"
            return backend
        except VaultError:
            raise
        except Exception:
            raise VaultError("access") from None

    @staticmethod
    def service(vault_id: str, key_id: str) -> str:
        return f"image-downloader.master.{vault_id}.{key_id}"

    def get(self, vault_id: str, key_id: str) -> bytes | None:
        try:
            value = self._backend().get_password(self.service(vault_id, key_id), "MASTER")
            return None if value is None else binary(value, 32)
        except VaultError:
            raise
        except Exception:
            raise VaultError("access") from None

    def create(self, vault_id: str, key_id: str, key: bytes) -> None:
        if len(key) != 32:
            raise VaultError("input")
        try:
            if self.get(vault_id, key_id) is not None:
                raise VaultError("corrupt")
            self._backend().set_password(self.service(vault_id, key_id), "MASTER", b64(key))
            if self.get(vault_id, key_id) != key:
                raise VaultError("access")
        except VaultError:
            raise
        except Exception:
            raise VaultError("access") from None

    def delete(self, vault_id: str, key_id: str) -> None:
        try:
            if self.get(vault_id, key_id) is not None:
                self._backend().delete_password(self.service(vault_id, key_id), "MASTER")
            if self.get(vault_id, key_id) is not None:
                raise VaultError("access")
        except VaultError:
            raise
        except Exception:
            raise VaultError("access") from None
