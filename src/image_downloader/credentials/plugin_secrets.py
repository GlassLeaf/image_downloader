"""Runtime-owned secret provider for site plugins."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType

from ..exceptions import CredentialAccessDeniedError, SecretNotFoundError
from ..ports import SecretProvider
from .profile_store import ProfileCredentialStore


class RuntimeSecrets(SecretProvider):
    def __init__(
        self,
        plugin_id: str,
        references: Mapping[str, str],
        *,
        store: ProfileCredentialStore,
        allowed: bool = True,
        guard: Callable[[], None] | None = None,
    ) -> None:
        self._plugin_id, self._references = plugin_id, MappingProxyType(dict(references))
        self._store = store
        self._allowed, self._guard = allowed, guard

    def get(self, name: str) -> str:
        if not self._allowed:
            raise CredentialAccessDeniedError(CredentialAccessDeniedError.reason)
        if self._guard is not None:
            self._guard()
        reference = self._references.get(name)
        if not reference:
            raise SecretNotFoundError("required plugin secret is not configured")
        return self._store.get_plugin(self._plugin_id, reference)
