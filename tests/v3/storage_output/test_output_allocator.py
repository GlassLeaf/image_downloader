from __future__ import annotations

import asyncio
import unicodedata
from pathlib import Path

import pytest

from image_downloader.config import AppConfig
from image_downloader.exceptions import ExistingFileConflictError
from image_downloader.models import Chapter, ImageResource
from image_downloader.runtime import OutputAllocator
from image_downloader.storage import FileSystem


def _config(mode: str, *, filename_format: str = "%NUM%.%EXT%", max_length: int | None = None) -> AppConfig:
    output: dict[str, object] = {"existing_file": mode, "filename_format": filename_format}
    if max_length is not None:
        output["max_component_length"] = max_length
    return AppConfig.model_validate({"output": output})


def _allocator(tmp_path: Path, config: AppConfig) -> tuple[FileSystem, OutputAllocator, Path]:
    filesystem = FileSystem(tmp_path.resolve())
    directory = Path("chapter")
    filesystem.ensure_directory(directory)
    return filesystem, OutputAllocator(filesystem, config), directory


@pytest.mark.parametrize(
    ("mode", "should_write", "expected_name"),
    (
        ("overwrite", True, "0001.jpeg"),
        ("skip", False, "0001.jpeg"),
        ("rename", True, "0001_1.jpeg"),
    ),
)
def test_existing_file_modes(tmp_path: Path, mode: str, should_write: bool, expected_name: str) -> None:
    async def scenario() -> None:
        filesystem, allocator, directory = _allocator(tmp_path, _config(mode))
        filesystem.write_bytes_atomic(directory / "0001.jpeg", b"existing")

        allocation = await allocator.allocate(
            directory, ImageResource("https://example.test/1"), Chapter(1, "one"), ".jpeg"
        )

        assert allocation.should_write is should_write
        assert allocation.relative_path.name == expected_name
        if allocation.should_write:
            await allocation.abort()

    asyncio.run(scenario())


def test_existing_file_error_mode(tmp_path: Path) -> None:
    async def scenario() -> None:
        filesystem, allocator, directory = _allocator(tmp_path, _config("error"))
        filesystem.write_bytes_atomic(directory / "0001.jpeg", b"existing")
        with pytest.raises(ExistingFileConflictError) as raised:
            await allocator.allocate(directory, ImageResource("https://example.test/1"), Chapter(1, "one"), ".jpeg")
        assert raised.value.code == "existing_file_conflict"
        assert raised.value.reason == "output file already exists and existing-file=error prevents overwrite"
        assert raised.value.relative_path == directory / "0001.jpeg"
        assert raised.value.policy == "error"

    asyncio.run(scenario())


def test_skip_allocation_has_no_terminal_reservation(tmp_path: Path) -> None:
    async def scenario() -> None:
        filesystem, allocator, directory = _allocator(tmp_path, _config("skip"))
        filesystem.write_bytes_atomic(directory / "0001.jpeg", b"existing")
        skipped = await allocator.allocate(
            directory, ImageResource("https://example.test/1"), Chapter(1, "one"), ".jpeg"
        )

        assert not skipped.should_write
        await skipped.commit()
        await skipped.abort()
        await skipped.commit()
        await skipped.abort()

    asyncio.run(scenario())


def test_reserved_allocation_requires_exactly_one_terminal_action(tmp_path: Path) -> None:
    async def scenario() -> None:
        _, allocator, directory = _allocator(tmp_path, _config("overwrite"))
        reserved = await allocator.allocate(
            directory, ImageResource("https://example.test/1"), Chapter(1, "one"), ".jpeg"
        )

        assert reserved.should_write
        await reserved.abort()
        with pytest.raises(RuntimeError, match="already been completed"):
            await reserved.commit()

    asyncio.run(scenario())


def test_concurrent_overwrite_allocations_are_renamed_instead_of_overwritten(tmp_path: Path) -> None:
    async def scenario() -> None:
        filesystem, allocator, directory = _allocator(tmp_path, _config("overwrite"))
        both_allocated = asyncio.Event()
        allocations = []

        async def save(data: bytes) -> Path:
            allocation = await allocator.allocate(
                directory, ImageResource("https://example.test/duplicate"), Chapter(1, "one"), ".jpeg"
            )
            allocations.append(allocation)
            if len(allocations) == 2:
                both_allocated.set()
            await both_allocated.wait()
            path = filesystem.write_bytes_atomic(allocation.relative_path, data)
            await allocation.commit()
            return path

        paths = await asyncio.gather(save(b"first"), save(b"second"))

        assert {path.name for path in paths} == {"0001.jpeg", "0001_1.jpeg"}
        assert {path.read_bytes() for path in paths} == {b"first", b"second"}

    asyncio.run(scenario())


def test_active_reservation_follows_rename_and_error_modes(tmp_path: Path) -> None:
    async def scenario() -> None:
        _, rename_allocator, rename_directory = _allocator(tmp_path / "rename", _config("rename"))
        first = await rename_allocator.allocate(
            rename_directory, ImageResource("https://example.test/1"), Chapter(1, "one"), ".jpeg"
        )
        second = await rename_allocator.allocate(
            rename_directory, ImageResource("https://example.test/2"), Chapter(1, "one"), ".jpeg"
        )
        assert second.relative_path.name == "0001_1.jpeg"
        await first.abort()
        await second.abort()

        _, error_allocator, error_directory = _allocator(tmp_path / "error", _config("error"))
        reserved = await error_allocator.allocate(
            error_directory, ImageResource("https://example.test/1"), Chapter(1, "one"), ".jpeg"
        )
        with pytest.raises(ExistingFileConflictError) as raised:
            await error_allocator.allocate(
                error_directory, ImageResource("https://example.test/2"), Chapter(1, "one"), ".jpeg"
            )
        assert raised.value.relative_path == error_directory / "0001.jpeg"
        assert raised.value.policy == "error"
        await reserved.abort()

    asyncio.run(scenario())


def test_skip_waits_for_commit_and_returns_the_committed_path(tmp_path: Path) -> None:
    async def scenario() -> None:
        filesystem, allocator, directory = _allocator(tmp_path, _config("skip"))
        first = await allocator.allocate(directory, ImageResource("https://example.test/1"), Chapter(1, "one"), ".jpeg")
        waiting = asyncio.create_task(
            allocator.allocate(directory, ImageResource("https://example.test/2"), Chapter(1, "one"), ".jpeg")
        )
        await asyncio.sleep(0)
        assert not waiting.done()

        filesystem.write_bytes_atomic(first.relative_path, b"saved")
        await first.commit()
        skipped = await waiting

        assert not skipped.should_write
        assert skipped.relative_path == first.relative_path

    asyncio.run(scenario())


def test_skip_retries_the_original_path_after_abort(tmp_path: Path) -> None:
    async def scenario() -> None:
        _, allocator, directory = _allocator(tmp_path, _config("skip"))
        first = await allocator.allocate(directory, ImageResource("https://example.test/1"), Chapter(1, "one"), ".jpeg")
        waiting = asyncio.create_task(
            allocator.allocate(directory, ImageResource("https://example.test/2"), Chapter(1, "one"), ".jpeg")
        )
        await asyncio.sleep(0)

        await first.abort()
        replacement = await waiting

        assert replacement.should_write
        assert replacement.relative_path == first.relative_path
        await replacement.abort()

    asyncio.run(scenario())


def test_portable_collision_key_matches_case_and_unicode_normalization(tmp_path: Path) -> None:
    async def scenario() -> None:
        filesystem, allocator, directory = _allocator(tmp_path, _config("skip", filename_format="%TITLE%.%EXT%"))
        existing_name = "Caf\N{LATIN SMALL LETTER E WITH ACUTE}.jpeg"
        filesystem.write_bytes_atomic(directory / existing_name, b"existing")
        requested_title = unicodedata.normalize("NFD", "CAF\N{LATIN SMALL LETTER E WITH ACUTE}")

        allocation = await allocator.allocate(
            directory,
            ImageResource("https://example.test/1"),
            Chapter(1, requested_title),
            ".jpeg",
        )

        assert not allocation.should_write
        assert allocation.relative_path.name == existing_name

    asyncio.run(scenario())


def test_collision_suffix_respects_optional_component_limit(tmp_path: Path) -> None:
    async def scenario() -> None:
        _, allocator, directory = _allocator(
            tmp_path,
            _config("overwrite", filename_format="%TITLE%.%EXT%", max_length=16),
        )
        chapter = Chapter(1, "abcdefghijk")
        first = await allocator.allocate(directory, ImageResource("https://example.test/1"), chapter, ".jpeg")
        second = await allocator.allocate(directory, ImageResource("https://example.test/2"), chapter, ".jpeg")

        assert first.relative_path != second.relative_path
        assert len(second.relative_path.name) <= 16
        assert second.relative_path.name.endswith("_1.jpeg")
        await first.abort()
        await second.abort()

    asyncio.run(scenario())
