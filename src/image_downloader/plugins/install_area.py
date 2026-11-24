"""Owned install staging areas; unfinished publication requires manual recovery."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..exceptions import ConfigurationError
from ..storage.filesystem import FileSystem
from ..storage.path_safety import existing_directory, existing_regular_file

INSTALL_AREA = ".id-install-transactions"
_OWNER = {"schema_version": 1, "owner": "image-downloader-plugin-install"}
_PHASES = {"prepared", "publishing", "committed", "rolled_back", "recovery_required"}


def _read_object(root: Path, name: str) -> dict[str, Any]:
    value = json.loads(FileSystem(root).read_text(Path(name)))
    if not isinstance(value, dict):
        raise ValueError("invalid install metadata")
    return value


def install_area(parent: Path, *, create: bool = False) -> Path | None:
    area = existing_directory(parent / INSTALL_AREA, "plugin install transaction area")
    if not area.exists():
        if not create:
            return None
        area.mkdir()
        FileSystem(area).write_bytes_atomic_new(Path("owner.json"), json.dumps(_OWNER).encode())
    try:
        owner = _read_object(area, "owner.json")
        if owner != _OWNER or type(owner.get("schema_version")) is not int:
            raise ValueError("unknown owner")
    except (OSError, ValueError, TypeError) as exc:
        raise ConfigurationError("plugin install transaction area has unknown ownership") from exc
    return area


def transaction_records(area: Path) -> list[tuple[Path, dict[str, Any]]]:
    records = []
    try:
        for child in area.iterdir():
            if child.name == "owner.json":
                continue
            if not re.fullmatch(r"[0-9a-f]{32}", child.name):
                raise ValueError("unknown install area entry")
            root = existing_directory(child, "plugin install transaction", required=True)
            expected = {"transaction.json", "catalog.before.json", "catalog.expected.json", "candidate", ".previous"}
            for path in root.iterdir():
                if path.name not in expected:
                    raise ValueError("unknown install transaction entry")
                if path.name in {"candidate", ".previous"}:
                    existing_directory(path, "plugin install transaction content", required=True)
                else:
                    existing_regular_file(path, "plugin install transaction metadata", required=True)
            value = _read_object(root, "transaction.json")
            if (
                value.get("schema_version") != 1
                or type(value.get("schema_version")) is not int
                or value.get("transaction_id") != child.name
                or value.get("phase") not in _PHASES
                or not isinstance(value.get("plugin_id"), str)
            ):
                raise ValueError("invalid install transaction")
            records.append((root, value))
    except (OSError, ValueError, TypeError) as exc:
        raise ConfigurationError("plugin install transaction metadata is invalid") from exc
    return records


def is_install_area(path: Path) -> bool:
    if path.name != INSTALL_AREA:
        return False
    area = install_area(path.parent)
    if area is not None:
        transaction_records(area)
    return True


def require_recovered_installs(plugin_root: Path) -> None:
    """Call under the catalog lock before any plugin-root mutation."""
    for kind in ("site_plugins", "image_processor_plugins"):
        parent = existing_directory(plugin_root / kind, "plugin kind directory")
        area = install_area(parent)
        if area is not None and any(
            value["phase"] in {"publishing", "recovery_required"} for _, value in transaction_records(area)
        ):
            raise ConfigurationError("plugin install requires manual recovery before further changes")
