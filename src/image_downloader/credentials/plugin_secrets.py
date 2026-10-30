"""Runtime-owned secret provider for site plugins."""

from __future__ import annotations

from collections.abc import Mapping

from ..exceptions import SecretNotFoundError
from ..ports import SecretProvider
from .profile_store import ProfileCredentialStore


class RuntimeSecrets(SecretProvider):
    def __init__(self, plugin_id: str, references: Mapping[str, str], *, store: ProfileCredentialStore) -> None:
        self.plugin_id, self.references = plugin_id, dict(references)
        self._store = store

    def get(self, name: str) -> str:
        reference = self.references.get(name)
        if not reference:
            raise SecretNotFoundError("required plugin secret is not configured")
        return self._store.get_plugin(self.plugin_id, reference)
