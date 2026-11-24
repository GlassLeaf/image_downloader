"""Keep an old plugin until publication or rollback has been verified."""

from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..exceptions import ConfigurationError
from ..storage.filesystem import FileSystem
from ..storage.path_safety import existing_directory
from .install_area import INSTALL_AREA, install_area, transaction_records
from .plugin_catalog import PluginCatalog, catalog_path
from .plugin_files import canonical_jcs
from .plugin_verification import plugin_content_digest


def warn_install_cleanup() -> None:
    try:
        sys.stderr.write("warning: plugin install data retained; inspect the install transaction before cleanup\n")
    except BaseException:
        pass


def _same_catalog(left: PluginCatalog, right: PluginCatalog) -> bool:
    return {entry.id: entry for entry in left.entries} == {entry.id: entry for entry in right.entries}


class PluginInstallTransaction:
    def __init__(self, plugin_root: Path, parent: Path, plugin_id: str) -> None:
        parent = existing_directory(parent, "plugin install target directory")
        parent.mkdir(parents=True, exist_ok=True)
        area = install_area(parent, create=True)
        assert area is not None
        self.plugin_root = plugin_root
        self.root = area / uuid4().hex
        self.root.mkdir()
        self.filesystem = FileSystem(self.root)
        self.staged, self.backup = self.root / "candidate", self.root / ".previous"
        self.finished = False
        self.phase = "prepared"
        self.new_digest: str | None = None
        self.old_digest: str | None = None
        self.before: bytes | None = None
        self.record: dict[str, Any] = {
            "schema_version": 1,
            "transaction_id": self.root.name,
            "plugin_id": plugin_id,
            "target_name": None,
            "new_content_digest": None,
            "old_content_digest": None,
            "old_catalog_present": False,
        }
        self._phase("prepared")

    def _phase(self, phase: str) -> None:
        self.phase = phase
        self.record["phase"] = phase
        self.filesystem.write_bytes_atomic(Path("transaction.json"), json.dumps(self.record).encode())

    def _prepare(self, target: Path, new: PluginCatalog) -> None:
        self.new_digest = plugin_content_digest(self.staged)
        self.old_digest = plugin_content_digest(target) if target.exists() else None
        catalog = catalog_path(self.plugin_root)
        if catalog.exists():
            filesystem = FileSystem(self.plugin_root)
            self.before = filesystem.read_bytes_bounded(Path("catalog.json"), catalog.stat().st_size)
            self.filesystem.write_bytes_atomic(Path("catalog.before.json"), self.before)
        self.record.update(
            target_name=target.name,
            new_content_digest=self.new_digest,
            old_content_digest=self.old_digest,
            old_catalog_present=self.before is not None,
        )
        self.filesystem.write_bytes_atomic(
            Path("catalog.expected.json"),
            canonical_jcs({"schema_version": 1, "plugins": [entry.as_json() for entry in new.entries]}),
        )
        self._phase("publishing")

    def _committed(self, target: Path, catalog: PluginCatalog) -> bool:
        return (
            target.exists()
            and plugin_content_digest(target) == self.new_digest
            and _same_catalog(PluginCatalog.load(catalog_path(self.plugin_root)), catalog)
        )

    def _restore_plugin(self, target: Path) -> None:
        if target.exists():
            current = plugin_content_digest(target)
            if self.old_digest is not None and current == self.old_digest and not self.backup.exists():
                return
            if current != self.new_digest or self.staged.exists():
                raise ConfigurationError("plugin install target changed during rollback")
            target.replace(self.staged)
        if self.old_digest is not None:
            if not self.backup.exists() or plugin_content_digest(self.backup) != self.old_digest:
                raise ConfigurationError("plugin install backup cannot be verified")
            self.backup.replace(target)
            if plugin_content_digest(target) != self.old_digest:
                raise ConfigurationError("plugin rollback could not be verified")

    def _restore_catalog(self, old: PluginCatalog, new: PluginCatalog) -> None:
        path = catalog_path(self.plugin_root)
        if not path.exists():
            if self.before is not None:
                raise ConfigurationError("plugin catalog disappeared during rollback")
            return
        current = PluginCatalog.load(path)
        if _same_catalog(current, old):
            return
        if not _same_catalog(current, new):
            raise ConfigurationError("plugin catalog changed during rollback")
        if self.before is None:
            path.unlink()
        else:
            FileSystem(self.plugin_root).write_bytes_atomic(Path("catalog.json"), self.before)

    def _recover(self, target: Path, old: PluginCatalog, new: PluginCatalog) -> bool:
        try:
            if self._committed(target, new):
                self.finished = True
                self._phase("committed")
                return True
        except BaseException:
            # Unknown state must never authorize removal of the backup.
            if self.finished:
                warn_install_cleanup()
                return True
        try:
            self._restore_plugin(target)
            self._restore_catalog(old, new)
            self._phase("rolled_back")
            self.finished = True
        except BaseException:
            try:
                self._phase("recovery_required")
            except BaseException:
                pass
            warn_install_cleanup()
        return False

    def publish(
        self,
        target: Path,
        old: PluginCatalog,
        new: PluginCatalog,
        write_catalog: Callable[[Path, PluginCatalog], None],
    ) -> None:
        self._prepare(target, new)
        try:
            if self.old_digest is not None:
                target.replace(self.backup)
            self.staged.replace(target)
            write_catalog(self.plugin_root, new)
            if not self._committed(target, new):
                raise ConfigurationError("plugin install publication could not be verified")
            self._phase("committed")
            self.finished = True
        except BaseException as primary:
            committed = self._recover(target, old, new)
            if not committed or not isinstance(primary, Exception):
                raise

    def close(self, *, primary: BaseException | None = None) -> None:
        if not self.finished and self.phase != "prepared":
            return
        try:
            area = install_area(self.root.parent.parent)
            if area is None or area.name != INSTALL_AREA or self.root.parent != area:
                raise ConfigurationError("plugin install transaction ownership changed")
            transaction_records(area)
            existing_directory(self.root, "plugin install transaction", required=True)
            value = json.loads(self.filesystem.read_text(Path("transaction.json")))
            if value.get("transaction_id") != self.root.name or value.get("plugin_id") != self.record["plugin_id"]:
                raise ConfigurationError("plugin install transaction ownership changed")
            expected = {"transaction.json", "catalog.before.json", "catalog.expected.json", "candidate", ".previous"}
            if any(path.name not in expected for path in self.root.iterdir()):
                raise ConfigurationError("plugin install transaction contains unknown data")
            # The checked absolute root is one owned transaction under the reserved area.
            shutil.rmtree(self.root)
        except BaseException as exc:
            warn_install_cleanup()
            if primary is None and not isinstance(exc, Exception):
                raise
