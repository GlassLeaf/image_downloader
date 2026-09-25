"""Plugin discovery, immutable records, class caching, and module cleanup."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import re
import sys
import types
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from types import MappingProxyType
from typing import Any

from ..exceptions import ConfigurationError, PluginError
from ..ports import ImageProcessor, SitePlugin, is_image_processor, is_site_plugin
from ..storage.path_safety import existing_directory
from .plugin_manifest import (
    CatalogEntry,
    PluginCatalog,
    PluginKind,
    PluginManifest,
    PluginVerificationMode,
    _is_link,
    _require_regular,
    author_config,
    catalog_path,
    read_manifest,
    verify_manifest,
    verify_plugin_tree,
)

_PLUGIN_MODULE_ROOT = "_image_downloader_plugins"
_MODULES_LOCK = RLock()


@dataclass(frozen=True, slots=True)
class PluginDiagnostic:
    name: str
    source: str
    loaded: bool
    detail: str = ""
    warning: bool = False


@dataclass(frozen=True, slots=True)
class PluginRecord:
    """Immutable description of a verified plugin.

    Imported classes and module names are deliberately owned by
    :class:`PluginClassLoader`, not by the registry snapshot.
    """

    manifest: PluginManifest
    author_defaults: Mapping[str, Any]
    catalog: CatalogEntry | None
    builtin: bool = False

    @property
    def id(self) -> str:
        return self.manifest.id

    @property
    def kind(self) -> PluginKind:
        return self.manifest.kind


@dataclass(frozen=True, slots=True)
class DiscoveredPlugin:
    kind: PluginKind
    directory: Path
    manifest: PluginManifest


@dataclass(frozen=True, slots=True)
class PluginDiscoveryResult:
    candidates: tuple[DiscoveredPlugin, ...]
    diagnostics: tuple[PluginDiagnostic, ...]


class PluginDiscovery:
    """Discover structurally valid manifests without making trust decisions."""

    def __init__(self, plugin_root: Path, *, mode: PluginVerificationMode = "strict") -> None:
        self.plugin_root = existing_directory(plugin_root, "plugin root")
        self.mode = mode

    def discover(self) -> PluginDiscoveryResult:
        diagnostics: list[PluginDiagnostic] = []
        if not self.plugin_root.exists():
            return PluginDiscoveryResult((), ())
        for item in self.plugin_root.iterdir():
            if item.name not in {"catalog.json", ".catalog.lock", "site_plugins", "image_processor_plugins"}:
                diagnostics.append(PluginDiagnostic(item.name, str(item), True, "unknown plugin-root entry", True))
        candidates: list[DiscoveredPlugin] = []
        for kind, directory in self._directories(diagnostics):
            try:
                manifest = read_manifest(directory, mode=self.mode)
                if manifest.kind != kind:
                    raise PluginError("manifest kind does not match containing directory")
                candidates.append(DiscoveredPlugin(kind, directory, manifest))
            except Exception as exc:
                diagnostics.append(
                    PluginDiagnostic(directory.name, str(directory), False, f"{type(exc).__name__}: {exc}")
                )
        ids = [candidate.manifest.id for candidate in candidates]
        if len(ids) != len(set(ids)):
            raise ConfigurationError("duplicate plugin manifest ID")
        return PluginDiscoveryResult(
            tuple(sorted(candidates, key=lambda item: item.manifest.id)),
            tuple(diagnostics),
        )

    def _directories(self, diagnostics: list[PluginDiagnostic]) -> list[tuple[PluginKind, Path]]:
        found: list[tuple[PluginKind, Path]] = []
        roots: tuple[tuple[str, PluginKind], ...] = (
            ("site_plugins", "site_plugin"),
            ("image_processor_plugins", "image_processor_plugin"),
        )
        for name, kind in roots:
            parent = self.plugin_root / name
            if not parent.exists():
                continue
            if _is_link(parent) or not parent.is_dir():
                diagnostics.append(PluginDiagnostic(name, str(parent), False, "plugin kind directory is invalid"))
                continue
            for item in parent.iterdir():
                if _is_link(item) or not item.is_dir():
                    diagnostics.append(
                        PluginDiagnostic(item.name, str(item), False, "plugin unit must be a non-link directory")
                    )
                else:
                    found.append((kind, item))
        return found


@dataclass(frozen=True, slots=True)
class PluginVerificationResult:
    records: tuple[PluginRecord, ...]
    catalog: PluginCatalog | None
    diagnostics: tuple[PluginDiagnostic, ...]


class PluginManifestVerifier:
    """Apply strict, warn, or off trust policy to discovered manifests."""

    def __init__(self, plugin_root: Path, *, mode: PluginVerificationMode) -> None:
        self.plugin_root = plugin_root
        self.mode = mode

    def verify(self, discovery: PluginDiscoveryResult) -> PluginVerificationResult:
        diagnostics: list[PluginDiagnostic] = []
        catalog = self._load_catalog(diagnostics)
        records: list[PluginRecord] = []
        for candidate in discovery.candidates:
            manifest = candidate.manifest
            try:
                pin = verify_manifest(manifest, catalog, mode=self.mode)
                records.append(PluginRecord(manifest, author_config(manifest), pin))
                diagnostics.append(PluginDiagnostic(manifest.id, str(candidate.directory), True))
            except Exception as exc:
                if self.mode == "warn" and isinstance(exc, PluginError):
                    try:
                        verify_plugin_tree(manifest)
                        records.append(PluginRecord(manifest, author_config(manifest), None))
                        diagnostics.append(
                            PluginDiagnostic(manifest.id, str(candidate.directory), True, str(exc), True)
                        )
                        continue
                    except Exception as nested:
                        exc = nested
                diagnostics.append(
                    PluginDiagnostic(manifest.id, str(candidate.directory), False, f"{type(exc).__name__}: {exc}")
                )
        if catalog:
            present = {record.id for record in records}
            for entry in catalog.entries:
                if not entry.revoked and entry.id not in present:
                    diagnostics.append(
                        PluginDiagnostic(
                            entry.id,
                            "catalog",
                            True,
                            "trusted catalog entry is not installed",
                            True,
                        )
                    )
        return PluginVerificationResult(tuple(records), catalog, tuple(diagnostics))

    def _load_catalog(self, diagnostics: list[PluginDiagnostic]) -> PluginCatalog | None:
        if self.mode in {"off", "bypass-all", "bypass-catalog"}:
            return None
        path = catalog_path(self.plugin_root)
        if not path.exists():
            return None
        try:
            return PluginCatalog.load(path)
        except ConfigurationError as exc:
            if self.mode == "strict":
                raise
            diagnostics.append(PluginDiagnostic("catalog", str(path), True, str(exc), True))
            return None


class PluginRegistry:
    """Read-only snapshot and lookup boundary for verified plugin records."""

    def __init__(
        self,
        records: Mapping[str, PluginRecord] | tuple[PluginRecord, ...] = (),
        *,
        catalog: PluginCatalog | None = None,
    ) -> None:
        snapshot = dict(records) if isinstance(records, Mapping) else {record.id: record for record in records}
        self._records = MappingProxyType(snapshot)
        self.catalog = catalog

    @property
    def records(self) -> Mapping[str, PluginRecord]:
        return self._records

    def get(self, plugin_id: str) -> PluginRecord | None:
        return self._records.get(plugin_id)


@dataclass(frozen=True, slots=True)
class _LoadedPlugin:
    plugin_class: type[object]
    module_namespace: str | None


class PluginClassLoader:
    """Own imported plugin classes and their process-global module entries."""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str, Path], _LoadedPlugin] = {}
        self._owned_namespaces: set[str] = set()
        self._instance_token = uuid.uuid4().hex[:12]
        self._lock = RLock()
        self._closed = False

    @staticmethod
    def _key(record: PluginRecord) -> tuple[str, str, Path]:
        return record.id, record.manifest.digest, record.manifest.directory.resolve()

    def register_class(self, record: PluginRecord, plugin_class: type[object]) -> None:
        """Register an in-process class, used by the built-in plugin and tests."""
        if not isinstance(plugin_class, type):
            raise PluginError("plugin entry class is invalid")
        with self._lock:
            self._ensure_open()
            self.unload(record)
            self._cache[self._key(record)] = _LoadedPlugin(plugin_class, None)

    def load_class(self, record: PluginRecord) -> type[object]:
        with self._lock:
            self._ensure_open()
            key = self._key(record)
            if loaded := self._cache.get(key):
                return loaded.plugin_class
            plugin_class, namespace = self._import_class(record)
            self._cache[key] = _LoadedPlugin(plugin_class, namespace)
            self._owned_namespaces.add(namespace)
            return plugin_class

    def _import_class(self, record: PluginRecord) -> tuple[type[object], str]:
        with _MODULES_LOCK:
            entry = record.manifest.value["entry"]
            filename = str(entry["file"])
            source = record.manifest.directory / filename
            _require_regular(source, "plugin entry must be a regular non-link file")
            plugin_name = re.sub(r"[^a-zA-Z0-9_]", "_", record.id)
            namespace = f"{_PLUGIN_MODULE_ROOT}.{plugin_name}_{record.manifest.digest[:12]}_{self._instance_token}"
            self._install_namespace_package(namespace, record.manifest.directory)
            module_name = f"{namespace}.entry"
            loader = importlib.machinery.SourceFileLoader(module_name, str(source))
            spec = importlib.util.spec_from_loader(module_name, loader)
            if spec is None:
                self._remove_namespace(namespace)
                raise PluginError("plugin entry source cannot be loaded")
            module = importlib.util.module_from_spec(spec)
            module.__package__ = namespace
            sys.modules[module_name] = module
            try:
                loader.exec_module(module)
                value = getattr(module, str(entry["class"]))
                if not isinstance(value, type):
                    raise PluginError("plugin entry class is invalid")
            except PluginError:
                self._remove_namespace(namespace)
                raise
            except Exception as exc:
                self._remove_namespace(namespace)
                raise PluginError("plugin entry import failed") from exc
            return value, namespace

    @staticmethod
    def _install_namespace_package(namespace: str, directory: Path) -> None:
        root = sys.modules.get(_PLUGIN_MODULE_ROOT)
        if root is None:
            root = types.ModuleType(_PLUGIN_MODULE_ROOT)
            root.__path__ = []
            root.__package__ = _PLUGIN_MODULE_ROOT
            root.__dict__["_image_downloader_owned"] = True
            sys.modules[_PLUGIN_MODULE_ROOT] = root
        package = types.ModuleType(namespace)
        package.__path__ = [str(directory)]
        package.__package__ = namespace
        sys.modules[namespace] = package

    @staticmethod
    def _remove_namespace(namespace: str) -> None:
        with _MODULES_LOCK:
            prefix = f"{namespace}."
            for name in tuple(sys.modules):
                if name == namespace or name.startswith(prefix):
                    sys.modules.pop(name, None)
            root = sys.modules.get(_PLUGIN_MODULE_ROOT)
            has_children = any(name.startswith(f"{_PLUGIN_MODULE_ROOT}.") for name in sys.modules)
            if root is not None and getattr(root, "_image_downloader_owned", False) and not has_children:
                sys.modules.pop(_PLUGIN_MODULE_ROOT, None)

    def module_namespace(self, record: PluginRecord) -> str | None:
        with self._lock:
            loaded = self._cache.get(self._key(record))
            return loaded.module_namespace if loaded else None

    def unload(self, record: PluginRecord) -> None:
        with self._lock:
            loaded = self._cache.pop(self._key(record), None)
            if loaded and loaded.module_namespace:
                self._owned_namespaces.discard(loaded.module_namespace)
                self._remove_namespace(loaded.module_namespace)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._cache.clear()
            for namespace in tuple(self._owned_namespaces):
                self._remove_namespace(namespace)
            self._owned_namespaces.clear()

    def _ensure_open(self) -> None:
        if self._closed:
            raise PluginError("plugin class loader is closed")

    def construct(self, record: PluginRecord) -> object:
        try:
            return self.load_class(record)()
        except PluginError:
            raise
        except Exception as exc:
            raise PluginError("plugin construction failed") from exc

    def site_instance(self, record: PluginRecord) -> SitePlugin:
        if record.kind != "site_plugin":
            raise PluginError("plugin record is not a site plugin")
        value = self.construct(record)
        if not is_site_plugin(value):
            raise PluginError("plugin class does not implement the v3 contract")
        return value

    def processor_instance(self, record: PluginRecord) -> ImageProcessor:
        if record.kind != "image_processor_plugin":
            raise PluginError("plugin record is not an image processor")
        value = self.construct(record)
        if not is_image_processor(value):
            raise PluginError("plugin class does not implement the v3 contract")
        return value

    def typed_instance(self, record: PluginRecord) -> SitePlugin | ImageProcessor:
        if record.kind == "site_plugin":
            return self.site_instance(record)
        return self.processor_instance(record)
