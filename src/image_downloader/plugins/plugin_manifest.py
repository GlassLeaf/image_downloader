"""Plugin manifest, signature, and trust-catalog boundaries."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast

import yaml
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from packaging.version import InvalidVersion, Version

from ..exceptions import ConfigurationError, PluginError
from ..immutable import freeze_json, thaw_json
from ..storage.interprocess_lock import InterProcessFileLock
from ..storage.path_safety import existing_directory

PluginKind = Literal["site_plugin", "image_processor_plugin"]
PluginConfigOverrides = Mapping[str, Mapping[str, Any]]
PluginVerificationMode = Literal[
    "strict",
    "warn",
    "off",
    "bypass-all",
    "bypass-catalog",
    "bypass-signature",
]
PluginVerificationOverride = Literal["bypass-all", "bypass-catalog", "bypass-signature"]
_KINDS: frozenset[str] = frozenset(("site_plugin", "image_processor_plugin"))
_MANIFEST_FIELDS = frozenset(
    (
        "schema_version",
        "id",
        "publisher",
        "version",
        "api_version",
        "kind",
        "capabilities",
        "match_priority",
        "entry",
        "config_file",
        "public_key",
        "key_id",
        "file_tree",
        "file_tree_sha256",
    )
)
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
_ID = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")
_SHA256 = re.compile(r"[0-9a-f]{64}$")


def effective_verification_mode(
    configured: Literal["strict", "warn", "off"],
    override: PluginVerificationOverride | None = None,
) -> PluginVerificationMode:
    """Resolve persisted verification policy and one-shot CLI override."""
    if override is not None:
        return override
    # ``off`` is intentionally the broad, explicitly configured escape hatch.
    return "bypass-all" if configured == "off" else configured


def canonical_jcs(value: object) -> bytes:
    """Canonical JSON bytes for the restricted JSON manifest schema."""
    return json.dumps(
        thaw_json(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_link(path: Path) -> bool:
    try:
        state = path.lstat()
    except OSError as exc:
        raise PluginError(f"cannot inspect plugin path: {path}") from exc
    return stat.S_ISLNK(state.st_mode) or bool(
        getattr(state, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _require_regular(path: Path, message: str) -> None:
    if _is_link(path) or not stat.S_ISREG(path.lstat().st_mode):
        raise PluginError(message)


def _relative_path(value: object, *, suffix: str | None = None) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise PluginError("manifest path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise PluginError("manifest path escapes plugin directory")
    if suffix and not value.endswith(suffix):
        raise PluginError(f"manifest path must end in {suffix}")
    return value


def _read_json(path: Path) -> object:
    _require_regular(path, "plugin metadata must be a regular non-link file")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PluginError("plugin JSON is invalid") from exc


@dataclass(frozen=True, slots=True)
class PluginManifest:
    value: Mapping[str, Any]
    signature: str | None
    digest: str
    directory: Path

    @property
    def id(self) -> str:
        return str(self.value["id"])

    @property
    def kind(self) -> PluginKind:
        return cast(PluginKind, self.value["kind"])


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    id: str
    kind: PluginKind
    manifest_digest: str
    selection_priority: int
    revoked: bool
    publisher: str | None = None
    version: str | None = None
    public_key: str | None = None
    key_id: str | None = None
    file_tree_sha256: str | None = None
    content_digest: str | None = None

    @property
    def content_pinned(self) -> bool:
        return self.content_digest is not None

    def as_json(self) -> dict[str, object]:
        if self.content_pinned:
            return {
                "id": self.id,
                "kind": self.kind,
                "manifest_digest": self.manifest_digest,
                "content_digest": self.content_digest,
                "selection_priority": self.selection_priority,
                "revoked": self.revoked,
            }
        return {
            "id": self.id,
            "kind": self.kind,
            "publisher": self.publisher,
            "version": self.version,
            "public_key": self.public_key,
            "key_id": self.key_id,
            "manifest_digest": self.manifest_digest,
            "file_tree_sha256": self.file_tree_sha256,
            "selection_priority": self.selection_priority,
            "revoked": self.revoked,
        }


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


def _manifest_file_tree(manifest: dict[str, Any], entry: dict[str, Any]) -> dict[str, str]:
    config_file = _relative_path(manifest["config_file"], suffix=".yaml")
    if not isinstance(manifest["file_tree"], dict) or not _SHA256.fullmatch(str(manifest["file_tree_sha256"])):
        raise PluginError("manifest file tree is invalid")
    tree: dict[str, str] = {}
    for name, digest in manifest["file_tree"].items():
        key = _relative_path(name)
        if not _SHA256.fullmatch(str(digest)):
            raise PluginError("manifest file tree hash is invalid")
        tree[key] = str(digest)
    if entry["file"] not in tree or config_file not in tree:
        raise PluginError("entry and author config must be included in file tree")
    if _sha256(canonical_jcs(tree)) != manifest["file_tree_sha256"]:
        raise PluginError("manifest file tree digest is invalid")
    return tree


def _validate_manifest(value: object, directory: Path) -> PluginManifest:
    if (
        not isinstance(value, dict)
        or set(value) != {"manifest", "signature"}
        or not isinstance(value["manifest"], dict)
        or not isinstance(value["signature"], str)
    ):
        raise PluginError("manifest wrapper is invalid")
    manifest = value["manifest"]
    if set(manifest) != _MANIFEST_FIELDS or manifest.get("schema_version") != 1 or manifest.get("api_version") != "3":
        raise PluginError("manifest schema/API is unsupported")
    plugin_id, publisher, kind = manifest["id"], manifest["publisher"], manifest["kind"]
    if (
        not isinstance(plugin_id, str)
        or not isinstance(publisher, str)
        or not _ID.fullmatch(plugin_id)
        or not _ID.fullmatch(publisher)
        or not plugin_id.startswith(publisher + ".")
        or kind not in _KINDS
    ):
        raise PluginError("manifest identity is invalid")
    try:
        Version(str(manifest["version"]))
    except InvalidVersion as exc:
        raise PluginError("manifest version is not PEP 440") from exc
    if (
        not isinstance(manifest["capabilities"], list)
        or any(not isinstance(item, str) for item in manifest["capabilities"])
        or type(manifest["match_priority"]) is not int
    ):
        raise PluginError("manifest capabilities or priority is invalid")
    entry = manifest["entry"]
    if (
        not isinstance(entry, dict)
        or set(entry) != {"file", "class"}
        or not isinstance(entry.get("class"), str)
        or not entry["class"]
    ):
        raise PluginError("manifest entry is invalid")
    _relative_path(entry["file"])
    _manifest_file_tree(manifest, entry)
    try:
        public = base64.b64decode(str(manifest["public_key"]), validate=True)
    except (TypeError, ValueError) as exc:
        raise PluginError("manifest public key is invalid") from exc
    if len(public) != 32 or _sha256(public) != manifest["key_id"] or not _SHA256.fullmatch(str(manifest["key_id"])):
        raise PluginError("manifest key ID is invalid")
    try:
        signature = base64.b64decode(value["signature"], validate=True)
    except (TypeError, ValueError) as exc:
        raise PluginError("manifest signature is invalid") from exc
    if len(signature) != 64:
        raise PluginError("manifest signature is invalid")
    return PluginManifest(
        cast(Mapping[str, Any], freeze_json(manifest)),
        value["signature"],
        _sha256(canonical_jcs(manifest)),
        directory,
    )


def _validate_relaxed_manifest(value: object, directory: Path) -> PluginManifest:
    """Read the minimum safe wrapper used by explicit bypass modes.

    Relaxed manifests are still data, not executable metadata: identity, entry
    path containment, and every on-disk file check remain enforced elsewhere.
    """
    if (
        not isinstance(value, dict)
        or "manifest" not in value
        or not set(value).issubset({"manifest", "signature"})
        or not isinstance(value["manifest"], dict)
    ):
        raise PluginError("manifest wrapper is invalid")
    signature = value.get("signature")
    if signature is not None and not isinstance(signature, str):
        raise PluginError("manifest signature is invalid")
    manifest = dict(value["manifest"])
    plugin_id, kind = manifest.get("id"), manifest.get("kind")
    if not isinstance(plugin_id, str) or not _ID.fullmatch(plugin_id) or kind not in _KINDS:
        raise PluginError("manifest identity is invalid")
    entry = manifest.get("entry")
    if (
        not isinstance(entry, dict)
        or set(entry) != {"file", "class"}
        or not isinstance(entry.get("class"), str)
        or not entry["class"]
    ):
        raise PluginError("manifest entry is invalid")
    _relative_path(entry["file"])
    if "match_priority" not in manifest:
        manifest["match_priority"] = 0
    elif type(manifest["match_priority"]) is not int:
        raise PluginError("manifest capabilities or priority is invalid")
    if "config_file" in manifest:
        _relative_path(manifest["config_file"], suffix=".yaml")
    return PluginManifest(
        cast(Mapping[str, Any], freeze_json(manifest)),
        signature,
        _sha256(canonical_jcs(manifest)),
        directory,
    )


def _relaxed_manifest_mode(mode: PluginVerificationMode) -> bool:
    return mode in {"off", "bypass-all", "bypass-signature"}


def read_manifest(directory: Path, *, mode: PluginVerificationMode = "strict") -> PluginManifest:
    try:
        directory = existing_directory(directory, "plugin directory", required=True)
    except ConfigurationError as exc:
        raise PluginError(str(exc)) from exc
    value = _read_json(directory / "manifest.json")
    return (
        _validate_relaxed_manifest(value, directory)
        if _relaxed_manifest_mode(mode)
        else _validate_manifest(value, directory)
    )


def collect_plugin_file_tree(directory: Path) -> dict[str, str]:
    """Collect exactly the files covered by a signed plugin manifest."""
    tree: dict[str, str] = {}
    for current, dirs, files in os.walk(directory, topdown=True, followlinks=False):
        current_path = Path(current)
        if _is_link(current_path):
            raise PluginError("plugin tree contains a link or reparse point")
        for name in dirs:
            candidate = current_path / name
            if _is_link(candidate):
                raise PluginError("plugin tree contains a link or reparse point")
        for name in files:
            candidate = current_path / name
            if _is_link(candidate):
                raise PluginError("plugin tree contains a link or reparse point")
            _require_regular(candidate, "plugin tree contains a non-regular file")
            relative = candidate.relative_to(directory).as_posix()
            if relative == "manifest.json" or (
                "__pycache__" in PurePosixPath(relative).parts and relative.endswith(".pyc")
            ):
                continue
            tree[relative] = _sha256(candidate.read_bytes())
    return dict(sorted(tree.items()))


def verify_plugin_tree(manifest: PluginManifest) -> None:
    """Verify the declared tree and its aggregate digest against local files."""
    actual = collect_plugin_file_tree(manifest.directory)
    declared_tree = manifest.value.get("file_tree")
    declared_digest = manifest.value.get("file_tree_sha256")
    if not isinstance(declared_tree, Mapping) or not isinstance(declared_digest, str):
        raise PluginError("manifest file tree is invalid")
    expected = {str(key): str(value) for key, value in declared_tree.items()}
    if actual != expected or _sha256(canonical_jcs(actual)) != declared_digest:
        raise PluginError("plugin file tree hash does not match")


def plugin_content_digest(directory: Path) -> str:
    """Return the canonical digest of every regular plugin file."""
    return _sha256(canonical_jcs(collect_plugin_file_tree(directory)))


def verify_plugin_signature(manifest: PluginManifest, *, public_key: str | None = None) -> None:
    """Verify a manifest signature with an explicitly pinned or declared key."""
    try:
        if manifest.signature is None:
            raise ValueError("signature missing")
        key = public_key or manifest.value.get("public_key")
        if not isinstance(key, str):
            raise ValueError("public key missing")
        public = base64.b64decode(key, validate=True)
        Ed25519PublicKey.from_public_bytes(public).verify(
            base64.b64decode(manifest.signature, validate=True), canonical_jcs(manifest.value)
        )
    except (TypeError, ValueError, InvalidSignature) as exc:
        raise PluginError("plugin signature is invalid") from exc


def verify_signed_plugin_source(manifest: PluginManifest) -> None:
    """Apply the complete source validation shared by trust and install."""
    verify_plugin_tree(manifest)
    verify_plugin_signature(manifest)


def _legacy_catalog_pin_matches(manifest: PluginManifest, entry: CatalogEntry) -> bool:
    return (
        not entry.content_pinned
        and entry.kind == manifest.kind
        and entry.publisher == manifest.value.get("publisher")
        and entry.version == manifest.value.get("version")
        and entry.public_key == manifest.value.get("public_key")
        and entry.key_id == manifest.value.get("key_id")
        and entry.manifest_digest == manifest.digest
        and entry.file_tree_sha256 == manifest.value.get("file_tree_sha256")
    )


def verify_manifest(
    manifest: PluginManifest,
    catalog: PluginCatalog | None,
    *,
    mode: PluginVerificationMode,
) -> CatalogEntry | None:
    """Validate one manifest under the selected CLI/runtime policy."""
    if mode in {"off", "bypass-all"}:
        return None
    if mode == "bypass-catalog":
        verify_signed_plugin_source(manifest)
        return None
    if mode in {"strict", "warn"}:
        verify_plugin_tree(manifest)
    if catalog is None:
        raise PluginError("plugin catalog is unavailable")
    entry = catalog.find(manifest.id)
    if entry is None or entry.revoked:
        raise PluginError("plugin is not trusted")
    if mode == "bypass-signature":
        if entry.content_pinned:
            matches = (
                entry.kind == manifest.kind
                and entry.manifest_digest == manifest.digest
                and entry.content_digest == plugin_content_digest(manifest.directory)
            )
        else:
            matches = _legacy_catalog_pin_matches(manifest, entry)
            if matches:
                verify_plugin_tree(manifest)
        if not matches:
            raise PluginError("plugin catalog pin does not match manifest")
        return entry
    if not _legacy_catalog_pin_matches(manifest, entry):
        raise PluginError("plugin catalog pin does not match manifest")
    verify_plugin_signature(manifest, public_key=entry.public_key)
    return entry


def author_config(manifest: PluginManifest) -> Mapping[str, Any]:
    config_file = manifest.value.get("config_file")
    if config_file is None:
        return cast(Mapping[str, Any], freeze_json({}))
    if not isinstance(config_file, str):
        raise PluginError("plugin author config must be a regular non-link file")
    path = manifest.directory / config_file
    _require_regular(path, "plugin author config must be a regular non-link file")
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise PluginError("plugin author YAML is invalid") from exc
    if not isinstance(value, dict) or set(value) != {"config"} or not isinstance(value["config"], dict):
        raise PluginError("plugin author YAML must contain only config mapping")
    return cast(Mapping[str, Any], freeze_json(value["config"]))
