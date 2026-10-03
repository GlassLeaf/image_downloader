from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from ..configuration.models import AppConfig
from ..exceptions import ConfigurationError, ExistingFileConflictError, OutputAllocationError
from ..immutable import freeze_json
from ..models import Chapter, DownloadManifest, ImageResource
from ..storage import FileSystem, safe_component
from .format_tokens import PLUGIN_TOKEN_PATTERN
from .original_filename import original_filename_parts

_CORE_TOKEN_PATTERN = re.compile(
    r"%(?P<core>CHAPTER_NUMBER|IMAGE_INDEX|CONTENT_TITLE|CHAPTER_TITLE|CHAPTER_SUBTITLE|EXT|"
    r"ORIGINAL_STEM|ORIGINAL_FILENAME|ORIGINAL_EXT)%"
)
_FORMAT_TOKEN_PATTERN = re.compile(rf"{PLUGIN_TOKEN_PATTERN.pattern}|{_CORE_TOKEN_PATTERN.pattern}")


@dataclass(frozen=True, slots=True)
class OutputFormatContext:
    """All immutable data used to expand one output path component."""

    manifest: DownloadManifest
    chapter: Chapter
    operation_url: str
    plugin_id: str
    image: ImageResource | None = None
    extension: str | None = None
    original_filename: str | None = None
    plugin_values: Mapping[str, Mapping[str, str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.manifest, DownloadManifest) or not isinstance(self.chapter, Chapter):
            raise TypeError("manifest and chapter must be their public DTO types")
        if not isinstance(self.operation_url, str) or not isinstance(self.plugin_id, str):
            raise TypeError("operation_url and plugin_id must be strings")
        if self.image is not None and not isinstance(self.image, ImageResource):
            raise TypeError("image must be ImageResource or None")
        if self.extension is not None and not isinstance(self.extension, str):
            raise TypeError("extension must be str or None")
        if self.original_filename is not None and not isinstance(self.original_filename, str):
            raise TypeError("original_filename must be str or None")
        values: dict[str, dict[str, str]] = {}
        for provider_id, mapping in self.plugin_values.items():
            if not isinstance(provider_id, str) or not isinstance(mapping, Mapping):
                raise TypeError("plugin_values must map plugin IDs to string mappings")
            materialized: dict[str, str] = {}
            for key, value in mapping.items():
                if not isinstance(key, str) or not isinstance(value, str):
                    raise TypeError("plugin_values must contain only string keys and values")
                materialized[key] = value
            values[provider_id] = materialized
        object.__setattr__(self, "plugin_values", cast(Mapping[str, Mapping[str, str]], freeze_json(values)))


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

    def chapter_directory(self, context: OutputFormatContext) -> Path:
        return Path(self._format_directory(self.config.output.directory_format, context))

    async def allocate(
        self,
        chapter_directory: Path,
        context: OutputFormatContext,
    ) -> OutputAllocation:
        image = context.image
        if image is None or context.extension is None:
            raise ValueError("filename allocation requires image and extension in OutputFormatContext")
        source_name = context.original_filename if context.original_filename is not None else image.original_filename
        base = chapter_directory / self._format_filename(
            self.config.output.filename_format,
            context,
            original_filename=source_name,
        )
        return await self.allocate_relative(base)

    async def allocate_relative(self, base: Path) -> OutputAllocation:
        """Reserve a validated managed relative path using the image collision policy."""
        self.filesystem.path(base)
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
                        raise ExistingFileConflictError(base)
                    else:
                        return self._reserve(self._unique_candidate(base))
                else:
                    existing = self._existing_collision(base)
                    if existing is not None:
                        if mode == "skip":
                            return OutputAllocation(existing)
                        if mode == "error":
                            raise ExistingFileConflictError(existing)
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

    def _format_directory(self, template: str, context: OutputFormatContext) -> str:
        return self._safe_format_component(self._render(template, context, extension="jpeg"))

    def _format_filename(
        self,
        template: str,
        context: OutputFormatContext,
        *,
        original_filename: str | None,
    ) -> str:
        image = context.image
        if image is None or context.extension is None:
            raise ValueError("filename formatting requires image and extension in OutputFormatContext")
        source_filename, source_stem, source_extension = original_filename_parts(original_filename, image.index)
        return self._safe_format_component(
            self._render(
                template,
                context,
                extension=context.extension.removeprefix("."),
                original_stem=source_stem,
                original_filename=source_filename,
                original_extension=source_extension,
            )
        )

    @staticmethod
    def _render(
        template: str,
        context: OutputFormatContext,
        *,
        extension: str,
        original_stem: str = "",
        original_filename: str = "",
        original_extension: str = "",
    ) -> str:
        image_index = f"{context.image.index:04d}" if context.image is not None else "%IMAGE_INDEX%"
        core_values = {
            "CHAPTER_NUMBER": f"{context.chapter.number:04d}",
            "IMAGE_INDEX": image_index,
            "CONTENT_TITLE": context.manifest.title,
            "CHAPTER_TITLE": context.chapter.title,
            "CHAPTER_SUBTITLE": context.chapter.subtitle,
            "EXT": extension,
            "ORIGINAL_STEM": original_stem,
            "ORIGINAL_FILENAME": original_filename,
            "ORIGINAL_EXT": original_extension,
        }

        def replace(match: re.Match[str]) -> str:
            plugin_id = match.groupdict().get("plugin_id")
            if plugin_id is not None:
                key = match.group("key")
                return context.plugin_values.get(plugin_id, {}).get(key, match.group(0))
            core = match.group("core")
            return core_values[core] if core is not None else match.group(0)

        return _FORMAT_TOKEN_PATTERN.sub(replace, template)

    def _safe_format_component(self, value: str) -> str:
        return safe_component(
            value.replace("__", "_").rstrip("_"),
            max_length=self.config.output.max_component_length,
        )
