"""Canonical plugin bytes and safe regular-file access."""

from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path

from ..exceptions import PluginError
from ..immutable import thaw_json


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


def _read_json(path: Path) -> object:
    _require_regular(path, "plugin metadata must be a regular non-link file")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PluginError("plugin JSON is invalid") from exc
