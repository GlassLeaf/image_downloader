"""Plugin install, trust and revoke transactions."""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..configuration.models import AppConfig
from ..exceptions import ConfigurationError, PluginError
from ..storage.path_safety import existing_directory
from .lifecycle import PluginRecord
from .plugin_manifest import (
    CatalogEntry,
    PluginCatalog,
    PluginManifest,
    PluginVerificationMode,
    _catalog_lock,
    _is_link,
    _write_catalog_unlocked,
    author_config,
    catalog_path,
    plugin_content_digest,
    read_manifest,
    verify_signed_plugin_source,
)
from .runtime import PluginRuntime


@dataclass(frozen=True, slots=True)
class PluginUninstallResult:
    id: str
    removed_directory: bool
    removed_catalog_entry: bool

    def as_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "removed_directory": self.removed_directory,
            "removed_catalog_entry": self.removed_catalog_entry,
        }


def catalog_entry_for(
    manifest: PluginManifest,
    *,
    selection_priority: int = 0,
    revoked: bool = False,
    content_pinned: bool = False,
) -> CatalogEntry:
    if content_pinned:
        return CatalogEntry(
            manifest.id,
            manifest.kind,
            manifest.digest,
            selection_priority,
            revoked,
            content_digest=plugin_content_digest(manifest.directory),
        )
    return CatalogEntry(
        manifest.id,
        manifest.kind,
        manifest.digest,
        selection_priority,
        revoked,
        str(manifest.value["publisher"]),
        str(manifest.value["version"]),
        str(manifest.value["public_key"]),
        str(manifest.value["key_id"]),
        str(manifest.value["file_tree_sha256"]),
    )


def _validated_catalog_entry(
    catalog: PluginCatalog,
    manifest: PluginManifest,
    *,
    selection_priority: int,
    content_pinned: bool,
) -> CatalogEntry:
    """Build an entry after applying the shared trust/install transition policy."""
    entry = catalog_entry_for(manifest, selection_priority=selection_priority, content_pinned=content_pinned)
    previous = catalog.find(entry.id)
    if previous and previous.kind != entry.kind:
        raise ConfigurationError("plugin kind or publisher cannot change")
    if (
        previous
        and previous.publisher is not None
        and entry.publisher is not None
        and previous.publisher != entry.publisher
    ):
        raise ConfigurationError("plugin kind or publisher cannot change")
    if (
        previous
        and previous.version == entry.version
        and (previous.manifest_digest != entry.manifest_digest or previous.file_tree_sha256 != entry.file_tree_sha256)
    ):
        raise ConfigurationError("plugin content cannot change without a version change")
    return entry


def _source_requires_signature(mode: PluginVerificationMode) -> bool:
    return mode not in {"bypass-all", "bypass-signature"}


def _content_pinned(mode: PluginVerificationMode) -> bool:
    return mode in {"bypass-all", "bypass-signature"}


def trust_plugin(
    plugin_root: Path,
    directory: Path,
    *,
    selection_priority: int = 0,
    mode: PluginVerificationMode = "strict",
) -> CatalogEntry:
    plugin_root = existing_directory(plugin_root, "plugin root")
    manifest = read_manifest(directory, mode=mode)
    if _source_requires_signature(mode):
        verify_signed_plugin_source(manifest)
    with _catalog_lock(plugin_root):
        old = PluginCatalog.load(catalog_path(plugin_root)) if catalog_path(plugin_root).exists() else PluginCatalog(())
        entry = _validated_catalog_entry(
            old,
            manifest,
            selection_priority=selection_priority,
            content_pinned=_content_pinned(mode),
        )
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
            existing.manifest_digest,
            existing.selection_priority,
            True,
            existing.publisher,
            existing.version,
            existing.public_key,
            existing.key_id,
            existing.file_tree_sha256,
            existing.content_digest,
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
                installed = read_manifest(candidate, mode="bypass-all")
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
    content_pinned: bool,
) -> CatalogEntry:
    with _catalog_lock(plugin_root):
        target = _installed_plugin_target(plugin_root, manifest, source_name)
        old_catalog = (
            PluginCatalog.load(catalog_path(plugin_root)) if catalog_path(plugin_root).exists() else PluginCatalog(())
        )
        entry = _validated_catalog_entry(
            old_catalog,
            manifest,
            selection_priority=selection_priority,
            content_pinned=content_pinned,
        )
        existing_directory(target.parent, "plugin install target directory")
        target.parent.mkdir(parents=True, exist_ok=True)
        existing_directory(target.parent, "plugin install target directory", required=True)
        try:
            if target.exists():
                old_manifest = read_manifest(target, mode="bypass-all")
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


def install_plugin(
    plugin_root: Path,
    source: Path,
    *,
    selection_priority: int = 0,
    mode: PluginVerificationMode = "strict",
) -> CatalogEntry:
    """Stage, verify, atomically install and trust one absolute source directory."""
    plugin_root = existing_directory(plugin_root, "plugin root")
    source = existing_directory(source, "plugin install source", required=True)
    manifest = read_manifest(source, mode=mode)
    # Validate before copy, then validate the staged snapshot to close the copy boundary.
    if _source_requires_signature(mode):
        verify_signed_plugin_source(manifest)
    plugin_root.mkdir(parents=True, exist_ok=True)
    stage_root = Path(tempfile.mkdtemp(prefix=".plugin-stage-", dir=plugin_root.parent))
    staged = stage_root / source.name
    backup = stage_root / ".previous"
    try:
        shutil.copytree(source, staged, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        staged_manifest = read_manifest(staged, mode=mode)
        if staged_manifest.id != manifest.id:
            raise PluginError("staged plugin ID changed")
        if _source_requires_signature(mode):
            verify_signed_plugin_source(staged_manifest)
        # Validate the class contract before either destination is committed.
        if mode != "bypass-all":
            checker = PluginRuntime(AppConfig(), stage_root, mode="bypass-all")
            candidate = PluginRecord(staged_manifest, author_config(staged_manifest), None)
            try:
                if candidate.kind == "site_plugin":
                    checker.site_instance(candidate)
                else:
                    checker.processor_instance(candidate)
            finally:
                checker.close()
        return _commit_staged_plugin(
            plugin_root,
            staged_manifest,
            staged,
            source.name,
            backup,
            selection_priority,
            _content_pinned(mode),
        )
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)


def _installed_directories(plugin_root: Path, plugin_id: str) -> list[Path]:
    found: list[Path] = []
    for parent_name in ("site_plugins", "image_processor_plugins"):
        parent = plugin_root / parent_name
        if not parent.exists():
            continue
        if _is_link(parent) or not parent.is_dir():
            raise ConfigurationError("plugin install target directory is invalid")
        for candidate in parent.iterdir():
            if _is_link(candidate) or not candidate.is_dir():
                continue
            try:
                manifest = read_manifest(candidate, mode="bypass-all")
            except PluginError:
                continue
            if manifest.id == plugin_id:
                found.append(candidate)
    return found


def uninstall_plugin(plugin_root: Path, plugin_id: str) -> PluginUninstallResult:
    """Remove one installed plugin and/or its stale catalog entry transactionally."""
    plugin_root = existing_directory(plugin_root, "plugin root", required=True)
    temporary_root: Path | None = None
    target: Path | None = None
    with _catalog_lock(plugin_root):
        catalog_file = catalog_path(plugin_root)
        catalog = PluginCatalog.load(catalog_file) if catalog_file.exists() else PluginCatalog(())
        matches = _installed_directories(plugin_root, plugin_id)
        if len(matches) > 1:
            raise ConfigurationError("plugin ID is installed in multiple directories")
        target = matches[0] if matches else None
        existing = catalog.find(plugin_id)
        if target is None and existing is None:
            raise ConfigurationError("plugin ID is not installed or trusted")
        try:
            if target is not None:
                temporary_root = Path(tempfile.mkdtemp(prefix=".plugin-uninstall-", dir=target.parent))
                target.replace(temporary_root / "removed")
            if existing is not None:
                _write_catalog_unlocked(plugin_root, catalog.remove(plugin_id))
        except Exception:
            removed = temporary_root / "removed" if temporary_root is not None else None
            if target is not None and removed is not None and removed.exists():
                removed.replace(target)
            raise
    if temporary_root is not None:
        shutil.rmtree(temporary_root, ignore_errors=True)
    return PluginUninstallResult(plugin_id, target is not None, existing is not None)
