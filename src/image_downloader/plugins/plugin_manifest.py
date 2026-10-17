"""Plugin manifest, signature, and trust-catalog boundaries."""

from __future__ import annotations

import base64
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast

import yaml
from packaging.version import InvalidVersion, Version

from image_downloader.plugins.plugin_files import _read_json, _require_regular, _sha256, canonical_jcs

from ..exceptions import ConfigurationError, PluginError
from ..immutable import freeze_json
from ..storage.path_safety import existing_directory

PluginKind = Literal["site_plugin", "image_processor_plugin"]
PluginConfigOverrides = Mapping[str, Mapping[str, Any]]
PluginDownloadPolicyOverrides = Mapping[str, Mapping[str, Any]]
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


def _relative_path(value: object, *, suffix: str | None = None) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise PluginError("manifest path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise PluginError("manifest path escapes plugin directory")
    if suffix and not value.endswith(suffix):
        raise PluginError(f"manifest path must end in {suffix}")
    return value


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
