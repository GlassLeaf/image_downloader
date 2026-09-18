"""Runtime-owned secret provider for site plugins."""

from __future__ import annotations

import os
from collections.abc import Mapping

from ..exceptions import SecretNotFound
from ..ports import SecretProvider
from .keyring import load_secret


class RuntimeSecrets(SecretProvider):
    def __init__(self, plugin_id: str, references: Mapping[str, str]) -> None:
        self.plugin_id, self.references = plugin_id, dict(references)

    def get(self, name: str) -> str:
        reference = self.references.get(name)
        if not reference:
            raise SecretNotFound("required plugin secret is not configured")
        env_plugin = "".join(char if char.isalnum() else "_" for char in self.plugin_id).upper()
        env_reference = "".join(char if char.isalnum() else "_" for char in reference).upper()
        value = os.getenv(f"IMAGE_DOWNLOADER_PLUGIN_{env_plugin}_{env_reference}")
        if value:
            return value
        value = load_secret(f"image-downloader.plugin.{self.plugin_id}", reference)
        if value is None:
            raise SecretNotFound("required plugin secret is unavailable")
        return value
