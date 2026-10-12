"""Source-only imports bound to each plugin's accepted content hashes."""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import os
import stat
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from types import CodeType, ModuleType

from ..exceptions import ConfigurationError, PluginError
from ..storage.path_safety import existing_directory
from .plugin_manifest import _is_link, _require_regular, _sha256


@dataclass(frozen=True, slots=True)
class _SourceContext:
    directory: Path
    hashes: Mapping[str, str] | None

    def check_directory(self, directory: Path) -> None:
        try:
            directory.relative_to(self.directory)
            existing_directory(directory, "plugin import directory", required=True)
        except (ValueError, ConfigurationError) as exc:
            raise PluginError("plugin import directory is unsafe") from exc

    def read_source(self, path: Path) -> bytes:
        try:
            relative = path.relative_to(self.directory).as_posix()
            self.check_directory(path.parent)
            _require_regular(path, "plugin source must be a regular non-link file")
            before = path.lstat()
            with path.open("rb") as stream:
                opened = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(opened.st_mode)
                    or getattr(opened, "st_nlink", 1) != 1
                    or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
                ):
                    raise PluginError("plugin source changed before read")
                source = stream.read()
                after = os.fstat(stream.fileno())
            self.check_directory(path.parent)
            current = path.lstat()
            if (
                _is_link(path)
                or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
                or (after.st_size, after.st_mtime_ns) != (opened.st_size, opened.st_mtime_ns)
            ):
                raise PluginError("plugin source changed during read")
        except (OSError, ValueError) as exc:
            raise PluginError("plugin source cannot be read safely") from exc
        if self.hashes is not None:
            expected = self.hashes.get(relative)
            if expected is None:
                raise PluginError("plugin source is not in the verified file tree")
            if _sha256(source) != expected:
                raise PluginError("plugin source hash does not match the verified file tree")
        return source


class _SourceLoader(importlib.machinery.SourceFileLoader):
    """Compile the same source bytes that passed the content check."""

    def __init__(self, fullname: str, path: str, context: _SourceContext) -> None:
        super().__init__(fullname, path)
        self._context = context

    def get_code(self, fullname: str) -> CodeType:
        filename = self.get_filename(fullname)
        source = self._context.read_source(Path(filename))
        return self.source_to_code(source, filename)


class _NamespaceFinder(importlib.abc.MetaPathFinder):
    """Mediate only the relative imports of active plugin namespaces."""

    def __init__(self) -> None:
        self._contexts: dict[str, _SourceContext] = {}
        self._lock = RLock()

    def register(self, namespace: str, context: _SourceContext) -> None:
        with self._lock:
            self._contexts[namespace] = context
            if self not in sys.meta_path:
                sys.meta_path.insert(0, self)

    def unregister(self, namespace: str) -> None:
        with self._lock:
            self._contexts.pop(namespace, None)
            if not self._contexts and self in sys.meta_path:
                sys.meta_path.remove(self)

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None,
        target: ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        with self._lock:
            context = next(
                (context for namespace, context in self._contexts.items() if fullname.startswith(f"{namespace}.")),
                None,
            )
        if context is None:
            return None
        if path is None:
            raise PluginError("plugin import package path is unavailable")
        for directory in path:
            context.check_directory(Path(directory))
        spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
        if spec is None:
            # Returning None would let another finder load unchecked plugin code.
            raise ModuleNotFoundError(f"No module named {fullname!r}", name=fullname)
        if isinstance(spec.loader, importlib.machinery.SourceFileLoader):
            spec.loader = _SourceLoader(fullname, spec.loader.path, context)
        elif spec.loader is None and spec.submodule_search_locations is not None:
            for directory in spec.submodule_search_locations:
                context.check_directory(Path(directory))
        else:
            raise PluginError("plugin modules must use Python source; bytecode and native extensions are unsupported")
        return spec


_NAMESPACE_FINDER = _NamespaceFinder()
