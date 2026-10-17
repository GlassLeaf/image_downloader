"""Trust catalog parsing, locking and atomic persistence."""

from __future__ import annotations

import base64
import json
import os
import tempfile
from pathlib import Path

from packaging.version import InvalidVersion, Version

from ..exceptions import ConfigurationError
from ..storage.interprocess_lock import InterProcessFileLock
from ..storage.path_safety import existing_directory
from .plugin_files import _require_regular, _sha256, canonical_jcs
from .plugin_manifest import _ID, _KINDS, _SHA256, CatalogEntry

_CATALOG_FIELDS = frozenset(("schema_version", "plugins"))


_CATALOG_ENTRY_FIELDS = frozenset(
    (
        "id",
        "kind",
        "publisher",
        "version",
        "public_key",
        "key_id",
        "manifest_digest",
        "file_tree_sha256",
        "selection_priority",
        "revoked",
    )
)


_CONTENT_PINNED_CATALOG_ENTRY_FIELDS = frozenset(
    (
        "id",
        "kind",
        "manifest_digest",
        "content_digest",
        "selection_priority",
        "revoked",
    )
)


class PluginCatalog:
    def __init__(self, entries: tuple[CatalogEntry, ...]) -> None:
        if len({entry.id for entry in entries}) != len(entries):
            raise ConfigurationError("plugin catalog contains duplicate IDs")
        self.entries = entries

    @classmethod
    def load(cls, path: Path) -> PluginCatalog:
        try:
            _require_regular(path, "plugin catalog must be a regular non-link file")
            value = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(value, dict)
                or set(value) != _CATALOG_FIELDS
                or value["schema_version"] != 1
                or not isinstance(value["plugins"], list)
            ):
                raise ValueError("schema")
            entries: list[CatalogEntry] = []
            for raw in value["plugins"]:
                if not isinstance(raw, dict):
                    raise ValueError("entry schema")
                if set(raw) == _CONTENT_PINNED_CATALOG_ENTRY_FIELDS:
                    kind = raw["kind"]
                    if (
                        kind not in _KINDS
                        or not _ID.fullmatch(str(raw["id"]))
                        or not all(_SHA256.fullmatch(str(raw[name])) for name in ("manifest_digest", "content_digest"))
                        or type(raw["selection_priority"]) is not int
                        or not isinstance(raw["revoked"], bool)
                    ):
                        raise ValueError("content-pinned entry values")
                    entries.append(
                        CatalogEntry(
                            str(raw["id"]),
                            kind,
                            str(raw["manifest_digest"]),
                            raw["selection_priority"],
                            raw["revoked"],
                            content_digest=str(raw["content_digest"]),
                        )
                    )
                    continue
                if set(raw) != _CATALOG_ENTRY_FIELDS:
                    raise ValueError("entry schema")
                kind = raw["kind"]
                if (
                    kind not in _KINDS
                    or not _ID.fullmatch(str(raw["id"]))
                    or not _ID.fullmatch(str(raw["publisher"]))
                    or not str(raw["id"]).startswith(str(raw["publisher"]) + ".")
                ):
                    raise ValueError("identity")
                Version(str(raw["version"]))
                if not all(
                    _SHA256.fullmatch(str(raw[name])) for name in ("key_id", "manifest_digest", "file_tree_sha256")
                ):
                    raise ValueError("hash")
                public = base64.b64decode(str(raw["public_key"]), validate=True)
                if (
                    len(public) != 32
                    or _sha256(public) != raw["key_id"]
                    or type(raw["selection_priority"]) is not int
                    or not isinstance(raw["revoked"], bool)
                ):
                    raise ValueError("entry values")
                entries.append(
                    CatalogEntry(
                        str(raw["id"]),
                        kind,
                        str(raw["manifest_digest"]),
                        raw["selection_priority"],
                        raw["revoked"],
                        str(raw["publisher"]),
                        str(raw["version"]),
                        str(raw["public_key"]),
                        str(raw["key_id"]),
                        str(raw["file_tree_sha256"]),
                    )
                )
        except (
            OSError,
            UnicodeDecodeError,
            TypeError,
            ValueError,
            KeyError,
            json.JSONDecodeError,
            InvalidVersion,
        ) as exc:
            raise ConfigurationError("plugin catalog is invalid") from exc
        return cls(tuple(entries))

    def find(self, plugin_id: str) -> CatalogEntry | None:
        return next((entry for entry in self.entries if entry.id == plugin_id), None)

    def replace(self, entry: CatalogEntry) -> PluginCatalog:
        return PluginCatalog(tuple(item for item in self.entries if item.id != entry.id) + (entry,))

    def remove(self, plugin_id: str) -> PluginCatalog:
        return PluginCatalog(tuple(item for item in self.entries if item.id != plugin_id))


def catalog_path(plugin_root: Path) -> Path:
    return plugin_root / "catalog.json"


def _catalog_lock(plugin_root: Path) -> InterProcessFileLock:
    plugin_root = existing_directory(plugin_root, "plugin root")
    plugin_root.mkdir(parents=True, exist_ok=True)
    plugin_root = existing_directory(plugin_root, "plugin root", required=True)
    return InterProcessFileLock(plugin_root / ".catalog.lock")


def _write_catalog_unlocked(plugin_root: Path, catalog: PluginCatalog) -> None:
    plugin_root = existing_directory(plugin_root, "plugin root")
    plugin_root.mkdir(parents=True, exist_ok=True)
    plugin_root = existing_directory(plugin_root, "plugin root", required=True)
    payload = canonical_jcs(
        {
            "schema_version": 1,
            "plugins": [entry.as_json() for entry in sorted(catalog.entries, key=lambda item: item.id)],
        }
    )
    descriptor, temporary = tempfile.mkstemp(prefix=".catalog-", suffix=".json", dir=plugin_root)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        Path(temporary).replace(catalog_path(plugin_root))
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_catalog(plugin_root: Path, catalog: PluginCatalog) -> None:
    with _catalog_lock(plugin_root):
        _write_catalog_unlocked(plugin_root, catalog)
