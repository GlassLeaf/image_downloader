"""Fail-closed checks for user-supplied filesystem boundaries.

The downloader deliberately does not treat a symbolic link or a Windows reparse
point as an interchangeable spelling of a configured path.  Configuration and
plugin roots are trust boundaries, so callers must validate the spelling *before*
calling :meth:`Path.resolve`.
"""

from __future__ import annotations

import stat
from pathlib import Path

from ..exceptions import ConfigurationError


def is_link_or_reparse(path: Path) -> bool:
    """Return whether an existing path is a link/reparse point without following it."""
    state = path.lstat()
    return stat.S_ISLNK(state.st_mode) or bool(
        getattr(state, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def canonical_path(path: Path, label: str) -> Path:
    """Validate every existing raw component, then return a non-strict canonical path.

    Missing descendants are allowed so explicit creation commands can create a
    fresh boundary.  An existing link/reparse point anywhere in the supplied
    spelling is rejected rather than silently followed.
    """
    if not path.is_absolute():
        raise ConfigurationError(f"{label} must be an absolute path")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        if part in {".", ".."}:
            raise ConfigurationError(f"{label} must not contain relative path components: {path}")
        current = current / part
        try:
            state = current.lstat()
        except FileNotFoundError:
            break
        except OSError as exc:
            raise ConfigurationError(f"could not inspect {label}: {path}") from exc
        if stat.S_ISLNK(state.st_mode) or bool(
            getattr(state, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            raise ConfigurationError(f"{label} must not contain a symbolic link or reparse point: {current}")
    return path.resolve(strict=False)


def existing_regular_file(path: Path, label: str, *, required: bool) -> Path | None:
    """Return a canonical existing regular file, or ``None`` for an optional absence."""
    resolved = canonical_path(path, label)
    try:
        state = path.lstat()
    except FileNotFoundError:
        if required:
            raise ConfigurationError(f"{label} not found: {path}") from None
        return None
    except OSError as exc:
        raise ConfigurationError(f"could not inspect {label}: {path}") from exc
    if is_link_or_reparse(path) or not stat.S_ISREG(state.st_mode):
        raise ConfigurationError(f"{label} must be a regular non-link file: {path}")
    return resolved


def existing_directory(path: Path, label: str, *, required: bool = False) -> Path:
    """Return a canonical directory, allowing a missing final directory when requested."""
    resolved = canonical_path(path, label)
    try:
        state = path.lstat()
    except FileNotFoundError:
        if required:
            raise ConfigurationError(f"{label} not found: {path}") from None
        return resolved
    except OSError as exc:
        raise ConfigurationError(f"could not inspect {label}: {path}") from exc
    if is_link_or_reparse(path) or not stat.S_ISDIR(state.st_mode):
        raise ConfigurationError(f"{label} must be a directory without links or reparse points: {path}")
    return resolved
