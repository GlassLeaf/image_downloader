from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path

from ..configuration.models import AppConfig
from ..exceptions import ConfigurationError, OutputAllocationError
from ..models import Chapter, ImageResource
from ..storage import FileSystem, safe_component


@dataclass(slots=True)
class OutputAllocation:
    """A reserved output path that must be committed or aborted by its owner."""

    relative_path: Path
    _allocator: OutputAllocator | None = None
    _key: str | None = None
    _completion: asyncio.Future[bool] | None = None
    _finished: bool = False

    @property
    def should_write(self) -> bool:
        return self._completion is not None

    async def commit(self) -> None:
        await self._finish(success=True)

    async def abort(self) -> None:
        await self._finish(success=False)

    async def _finish(self, *, success: bool) -> None:
        if self._completion is None:
            return
        if self._finished or self._allocator is None or self._key is None:
            raise RuntimeError("output allocation has already been completed")
        await self._allocator._finish(
            self.relative_path,
            self._key,
            self._completion,
            success=success,
        )
        self._finished = True


class OutputAllocator:
    def __init__(self, filesystem: FileSystem, config: AppConfig) -> None:
        self.filesystem = filesystem
        self.config = config
        self._lock = asyncio.Lock()
        self._reserved: dict[str, asyncio.Future[bool]] = {}
        self._known_files: dict[str, Path] = {}
        self._loaded_parents: set[str] = set()
        self._committed_by_operation: set[str] = set()

    async def refresh_directory(self, directory: Path) -> None:
        """Re-read the directory while the caller holds its inter-process lock."""
        async with self._lock:
            parent_key = self.filesystem.collision_key(directory)
            self._known_files = {
                key: path
                for key, path in self._known_files.items()
                if self.filesystem.collision_key(path.parent) != parent_key
            }
            self._loaded_parents.discard(parent_key)
            for child in self.filesystem.child_paths(directory):
                self._known_files.setdefault(self.filesystem.collision_key(child), child)
            self._loaded_parents.add(parent_key)

    def chapter_directory(self, chapter: Chapter) -> Path:
        return Path(
            self._format(
                self.config.output.directory_format,
                chapter.number,
                chapter.title,
                chapter.subtitle,
            )
        )

    async def allocate(
        self,
        chapter_directory: Path,
        image: ImageResource,
        chapter: Chapter,
        extension: str,
    ) -> OutputAllocation:
        base = chapter_directory / self._format(
            self.config.output.filename_format,
            image.index,
            chapter.title,
            chapter.subtitle,
            extension,
        )
        mode = self.config.output.existing_file
        while True:
            waiter: asyncio.Future[bool] | None = None
            async with self._lock:
                key = self.filesystem.collision_key(base)
                waiter = self._reserved.get(key)
                if waiter is not None:
                    if mode == "skip":
                        pass
                    elif mode == "error":
                        raise FileExistsError(str(base))
                    else:
                        return self._reserve(self._unique_candidate(base))
                else:
                    existing = self._existing_collision(base)
                    if existing is not None:
                        if mode == "skip":
                            return OutputAllocation(existing)
                        if mode == "error":
                            raise FileExistsError(str(existing))
                        if mode == "rename":
                            return self._reserve(self._unique_candidate(base))
                        if key in self._committed_by_operation:
                            return self._reserve(self._unique_candidate(base))
                        return self._reserve(existing)
                    return self._reserve(base)

            if await asyncio.shield(waiter):
                existing = self._existing_collision(base)
                if existing is not None:
                    return OutputAllocation(existing)

    def _reserve(self, candidate: Path) -> OutputAllocation:
        key = self.filesystem.collision_key(candidate)
        completion = asyncio.get_running_loop().create_future()
        self._reserved[key] = completion
        return OutputAllocation(candidate, self, key, completion)

    async def _finish(
        self,
        relative_path: Path,
        key: str,
        completion: asyncio.Future[bool],
        *,
        success: bool,
    ) -> None:
        async with self._lock:
            if self._reserved.get(key) is not completion:
                raise RuntimeError("output allocation reservation is no longer active")
            del self._reserved[key]
            if success:
                self._known_files[key] = relative_path
                self._committed_by_operation.add(key)
            completion.set_result(success)

    def _existing_collision(self, candidate: Path) -> Path | None:
        parent_key = self.filesystem.collision_key(candidate.parent)
        if parent_key not in self._loaded_parents:
            children = sorted(
                self.filesystem.child_paths(candidate.parent),
                key=lambda path: (path.name != candidate.name, path.name),
            )
            for child in children:
                key = self.filesystem.collision_key(child)
                self._known_files.setdefault(key, child)
            self._loaded_parents.add(parent_key)
        key = self.filesystem.collision_key(candidate)
        existing = self._known_files.get(key)
        if existing is not None:
            if self.filesystem.exists(existing):
                return existing
            del self._known_files[key]
        if self.filesystem.exists(candidate):
            self._known_files[key] = candidate
            return candidate
        return None

    def _unique_candidate(self, base: Path) -> Path:
        for suffix in range(1, 10000):
            candidate = self._renamed_candidate(base, suffix)
            key = self.filesystem.collision_key(candidate)
            if key not in self._reserved and self._existing_collision(candidate) is None:
                return candidate
        raise OutputAllocationError("could not allocate a unique output filename")

    def _renamed_candidate(self, candidate: Path, suffix: int) -> Path:
        tail = f"_{suffix}{candidate.suffix}"
        limit = self.config.output.max_component_length
        if limit is None:
            return candidate.with_name(f"{candidate.stem}{tail}")
        available = limit - len(tail)
        if available <= 0:
            raise ConfigurationError("output component limit is too short for a collision suffix")
        stem = candidate.stem
        if len(stem) > available:
            digest = hashlib.sha256(candidate.name.encode("utf-8")).hexdigest()[:8]
            stem = f"{stem[: available - 9].rstrip(' .')}_{digest}" if available > 9 else digest[:available]
        return candidate.with_name(f"{stem}{tail}")

    def _format(
        self,
        template: str,
        number: int,
        title: str,
        subtitle: str,
        extension: str = ".jpeg",
    ) -> str:
        value = (
            template.replace("%NUM%", f"{number:04d}")
            .replace("%TITLE%", title)
            .replace("%SUBTITLE%", subtitle)
            .replace("%EXT%", extension.removeprefix("."))
        )
        return safe_component(
            value.replace("__", "_").rstrip("_"),
            max_length=self.config.output.max_component_length,
        )
