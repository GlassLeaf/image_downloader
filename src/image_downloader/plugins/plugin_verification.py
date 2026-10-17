"""Plugin tree, signature and configured trust-policy verification."""

from __future__ import annotations

import base64
import os
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from ..exceptions import PluginError
from .plugin_catalog import PluginCatalog
from .plugin_files import _is_link, _require_regular, _sha256, canonical_jcs
from .plugin_manifest import CatalogEntry, PluginManifest, PluginVerificationMode


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
    _verified_plugin_tree(manifest)


def _verified_plugin_tree(manifest: PluginManifest) -> dict[str, str]:
    """Return the exact tree accepted by the declared content hashes."""
    actual = collect_plugin_file_tree(manifest.directory)
    declared_tree = manifest.value.get("file_tree")
    declared_digest = manifest.value.get("file_tree_sha256")
    if not isinstance(declared_tree, Mapping) or not isinstance(declared_digest, str):
        raise PluginError("manifest file tree is invalid")
    expected = {str(key): str(value) for key, value in declared_tree.items()}
    if actual != expected or _sha256(canonical_jcs(actual)) != declared_digest:
        raise PluginError("plugin file tree hash does not match")
    return actual


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
    entry, _ = _verify_manifest_with_tree(manifest, catalog, mode=mode)
    return entry


def _verify_manifest_with_tree(
    manifest: PluginManifest,
    catalog: PluginCatalog | None,
    *,
    mode: PluginVerificationMode,
) -> tuple[CatalogEntry | None, dict[str, str] | None]:
    """Keep the very same tree used to accept a signature or content pin."""
    if mode in {"off", "bypass-all"}:
        return None, None
    actual: dict[str, str] | None = None
    if mode == "bypass-catalog":
        actual = _verified_plugin_tree(manifest)
        verify_plugin_signature(manifest)
        return None, actual
    if mode in {"strict", "warn"}:
        actual = _verified_plugin_tree(manifest)
    if catalog is None:
        raise PluginError("plugin catalog is unavailable")
    entry = catalog.find(manifest.id)
    if entry is None or entry.revoked:
        raise PluginError("plugin is not trusted")
    if mode == "bypass-signature":
        if entry.content_pinned:
            actual = collect_plugin_file_tree(manifest.directory)
            matches = (
                entry.kind == manifest.kind
                and entry.manifest_digest == manifest.digest
                and entry.content_digest == _sha256(canonical_jcs(actual))
            )
        else:
            matches = _legacy_catalog_pin_matches(manifest, entry)
            if matches:
                actual = _verified_plugin_tree(manifest)
        if not matches:
            raise PluginError("plugin catalog pin does not match manifest")
        return entry, actual
    if not _legacy_catalog_pin_matches(manifest, entry):
        raise PluginError("plugin catalog pin does not match manifest")
    verify_plugin_signature(manifest, public_key=entry.public_key)
    return entry, actual
