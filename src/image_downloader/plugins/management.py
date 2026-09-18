"""Plugin install, trust and revoke transactions."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from ..configuration.models import AppConfig
from ..exceptions import ConfigurationError, PluginError
from ..storage.path_safety import existing_directory
from .lifecycle import PluginRecord
from .plugin_manifest import (
    CatalogEntry,
    PluginCatalog,
    PluginManifest,
    _catalog_lock,
    _is_link,
    _write_catalog_unlocked,
    author_config,
    catalog_path,
    read_manifest,
    verify_signed_plugin_source,
)
from .runtime import PluginRuntime


def catalog_entry_for(manifest: PluginManifest, *, selection_priority: int = 0, revoked: bool = False) -> CatalogEntry:
    return CatalogEntry(
        manifest.id,
        manifest.kind,
        str(manifest.value["publisher"]),
        str(manifest.value["version"]),
        str(manifest.value["public_key"]),
        str(manifest.value["key_id"]),
        manifest.digest,
        str(manifest.value["file_tree_sha256"]),
        selection_priority,
        revoked,
    )


def _validated_catalog_entry(
    catalog: PluginCatalog,
    manifest: PluginManifest,
    *,
    selection_priority: int,
) -> CatalogEntry:
    """Build an entry after applying the shared trust/install transition policy."""
    entry = catalog_entry_for(manifest, selection_priority=selection_priority)
    previous = catalog.find(entry.id)
    if previous and (previous.kind != entry.kind or previous.publisher != entry.publisher):
        raise ConfigurationError("plugin kind or publisher cannot change")
    if (
        previous
        and previous.version == entry.version
        and (previous.manifest_digest != entry.manifest_digest or previous.file_tree_sha256 != entry.file_tree_sha256)
    ):
        raise ConfigurationError("plugin content cannot change without a version change")
    return entry


def trust_plugin(plugin_root: Path, directory: Path, *, selection_priority: int = 0) -> CatalogEntry:
    plugin_root = existing_directory(plugin_root, "plugin root")
    manifest = read_manifest(directory)
    verify_signed_plugin_source(manifest)
    with _catalog_lock(plugin_root):
        old = PluginCatalog.load(catalog_path(plugin_root)) if catalog_path(plugin_root).exists() else PluginCatalog(())
        entry = _validated_catalog_entry(old, manifest, selection_priority=selection_priority)
        _write_catalog_unlocked(plugin_root, old.replace(entry))
    return entry


def revoke_plugin(plugin_root: Path, plugin_id: str) -> CatalogEntry:
    plugin_root = existing_directory(plugin_root, "plugin root", required=True)
    with _catalog_lock(plugin_root):
        catalog = PluginCatalog.load(catalog_path(plugin_root))
        existing = catalog.find(plugin_id)
        if existing is None:
            raise ConfigurationError("plugin ID is not trusted")
        entry = CatalogEntry(
            existing.id,
            existing.kind,
            existing.publisher,
            existing.version,
            existing.public_key,
            existing.key_id,
            existing.manifest_digest,
            existing.file_tree_sha256,
            existing.selection_priority,
            True,
        )
        _write_catalog_unlocked(plugin_root, catalog.replace(entry))
    return entry


def _installed_plugin_target(plugin_root: Path, manifest: PluginManifest, source_name: str) -> Path:
    parent_name = "site_plugins" if manifest.kind == "site_plugin" else "image_processor_plugins"
    parent = plugin_root / parent_name
    matches: list[Path] = []
    if parent.exists():
        if _is_link(parent) or not parent.is_dir():
            raise ConfigurationError("plugin install target directory is invalid")
        for candidate in parent.iterdir():
            if _is_link(candidate) or not candidate.is_dir():
                continue
            try:
                installed = read_manifest(candidate)
            except PluginError:
                continue
            if installed.id == manifest.id:
                matches.append(candidate)
    if len(matches) > 1:
        raise ConfigurationError("plugin ID is installed in multiple directories")
    return matches[0] if matches else parent / source_name


def _commit_staged_plugin(
    plugin_root: Path,
    manifest: PluginManifest,
    staged: Path,
    source_name: str,
    backup: Path,
    selection_priority: int,
) -> CatalogEntry:
    with _catalog_lock(plugin_root):
        target = _installed_plugin_target(plugin_root, manifest, source_name)
        old_catalog = (
            PluginCatalog.load(catalog_path(plugin_root)) if catalog_path(plugin_root).exists() else PluginCatalog(())
        )
        entry = _validated_catalog_entry(old_catalog, manifest, selection_priority=selection_priority)
        existing_directory(target.parent, "plugin install target directory")
        target.parent.mkdir(parents=True, exist_ok=True)
        existing_directory(target.parent, "plugin install target directory", required=True)
        try:
            if target.exists():
                old_manifest = read_manifest(target)
                if old_manifest.id != manifest.id:
                    raise ConfigurationError("plugin target directory is owned by a different ID")
                target.replace(backup)
            staged.replace(target)
            _write_catalog_unlocked(plugin_root, old_catalog.replace(entry))
        except Exception:
            if target.exists():
                target.replace(staged)
            if backup.exists():
                backup.replace(target)
            raise
        return entry


def install_plugin(plugin_root: Path, source: Path, *, selection_priority: int = 0) -> CatalogEntry:
    """Stage, verify, atomically install and trust one absolute source directory."""
    plugin_root = existing_directory(plugin_root, "plugin root")
    source = existing_directory(source, "plugin install source", required=True)
    manifest = read_manifest(source)
    # Validate before copy, then validate the staged snapshot to close the copy boundary.
    verify_signed_plugin_source(manifest)
    plugin_root.mkdir(parents=True, exist_ok=True)
    stage_root = Path(tempfile.mkdtemp(prefix=".plugin-stage-", dir=plugin_root.parent))
    staged = stage_root / source.name
    backup = stage_root / ".previous"
    try:
        shutil.copytree(source, staged, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        staged_manifest = read_manifest(staged)
        if staged_manifest.id != manifest.id:
            raise PluginError("staged plugin ID changed")
        verify_signed_plugin_source(staged_manifest)
        # Validate the class contract before either destination is committed.
        checker = PluginRuntime(AppConfig(), stage_root, mode="off")
        candidate = PluginRecord(staged_manifest, author_config(staged_manifest), None)
        try:
            if candidate.kind == "site_plugin":
                checker.site_instance(candidate)
            else:
                checker.processor_instance(candidate)
        finally:
            checker.close()
        return _commit_staged_plugin(plugin_root, staged_manifest, staged, source.name, backup, selection_priority)
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)
