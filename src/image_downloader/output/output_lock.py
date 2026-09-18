"""Directory-scoped output locks shared by cooperating local processes."""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from ..observability.logging import DownloadLogger
from ..storage import FileSystem
from ..storage.interprocess_lock import InterProcessFileLock


class OutputDirectoryLocks:
    def __init__(self, state: FileSystem, *, timeout_seconds: float, logger: DownloadLogger) -> None:
        self.state = state
        self.timeout_seconds = timeout_seconds
        self.logger = logger

    def lock_path(self, directory: Path) -> Path:
        # Absolute output paths include the profile and optional plugin namespace.
        # Canonical spelling makes this key portable across case-sensitive hosts.
        key = unicodedata.normalize("NFC", directory.absolute().as_posix()).casefold()
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        relative = Path("output-locks") / f"{digest}.lock"
        self.state.ensure_directory(relative.parent)
        self.state.exists(relative)  # Reject an existing symlink/reparse point.
        return self.state.path(relative)

    @asynccontextmanager
    async def hold(self, directory: Path) -> AsyncIterator[None]:
        lock = InterProcessFileLock(self.lock_path(directory), timeout_seconds=self.timeout_seconds)
        await lock.acquire_async()
        try:
            yield
        except BaseException:
            try:
                lock.release()
            except Exception as exc:
                try:
                    await self.logger.core(
                        "operation_failed",
                        module="storage",
                        action="output_lock_release",
                        error=exc,
                        debug=True,
                    )
                except Exception:
                    pass
            raise
        else:
            lock.release()
