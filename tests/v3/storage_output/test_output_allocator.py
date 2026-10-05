from __future__ import annotations

import asyncio
import unicodedata
from pathlib import Path

import pytest

from image_downloader.config import AppConfig
from image_downloader.exceptions import ExistingFileConflictError
from image_downloader.models import Chapter, DownloadManifest, ImageResource
from image_downloader.runtime import OutputAllocator, OutputFormatContext
from image_downloader.storage import FileSystem


def test_visually_similar_replacements_and_extension_roundtrip(tmp_path: Path) -> None:
    from image_downloader.storage import safe_name

    assert safe_name('"*/:<>?\\|') == "”＊／：＜＞？＼｜"
    assert safe_name("ordinary.png") == "ordinary.png"
    assert safe_name("CON.txt") == "_CON.txt"
    assert safe_name("control\x00name") == "control_name"

    async def scenario() -> None:
        config = AppConfig.model_validate(
            {"output": {"directory_format": "%CONTENT_TITLE%", "filename_format": "%CHAPTER_TITLE%.%EXT%"}}
        )
        allocator = OutputAllocator(FileSystem(tmp_path), config)
        chapter = Chapter(1, 'v1.2."*/:<>?\\|')
        manifest = DownloadManifest(chapter.title, (chapter,))
        context = OutputFormatContext(
            manifest, chapter, "https://example.test/", "test.site", ImageResource("image:1"), ".png"
        )
        directory = allocator.chapter_directory(context)
        assert directory.name == "v1．2．”＊／：＜＞？＼｜"
        allocator.filesystem.ensure_directory(directory)
        allocation = await allocator.allocate(directory, context)
        assert allocation.relative_path.name == "v1．2．”＊／：＜＞？＼｜.png"
        allocator.filesystem.write_bytes_atomic(allocation.relative_path, b"original")
        assert allocator.filesystem.read_bytes_bounded(allocation.relative_path, 8) == b"original"
        await allocation.commit()
        assert manifest.title == chapter.title

    asyncio.run(scenario())


def test_title_dots_are_converted_without_an_extension_token(tmp_path: Path) -> None:
    allocator = OutputAllocator(FileSystem(tmp_path), _config("overwrite", filename_format="%CHAPTER_TITLE%"))
    chapter = Chapter(1, "v1.2.")
    result = allocator._format_filename(
        "%CHAPTER_TITLE%", _context(chapter, ImageResource("image:1"), ".png"), original_filename=None
    )
    assert result == "v1．2．"


def _config(mode: str, *, filename_format: str = "%IMAGE_INDEX%.%EXT%", max_length: int | None = None) -> AppConfig:
    output: dict[str, object] = {"existing_file": mode, "filename_format": filename_format}
    if max_length is not None:
        output["max_component_length"] = max_length
    return AppConfig.model_validate({"output": output})


def _allocator(tmp_path: Path, config: AppConfig) -> tuple[FileSystem, OutputAllocator, Path]:
    filesystem = FileSystem(tmp_path.resolve())
    directory = Path("chapter")
    filesystem.ensure_directory(directory)
    return filesystem, OutputAllocator(filesystem, config), directory


def _context(
    chapter: Chapter,
    image: ImageResource | None = None,
    extension: str | None = None,
    *,
    original_filename: str | None = None,
    plugin_values: dict[str, dict[str, str]] | None = None,
) -> OutputFormatContext:
    return OutputFormatContext(
        DownloadManifest("content", (chapter,)),
        chapter,
        "https://example.test/content",
        "com.example.site",
        image,
        extension,
        original_filename,
        plugin_values or {},
    )


def _directory(allocator: OutputAllocator, chapter: Chapter) -> Path:
    return allocator.chapter_directory(_context(chapter))


async def _allocate(
    allocator: OutputAllocator,
    directory: Path,
    image: ImageResource,
    chapter: Chapter,
    extension: str,
    *,
    original_filename: str | None = None,
) -> object:
    return await allocator.allocate(
        directory,
        _context(chapter, image, extension, original_filename=original_filename),
    )


def test_output_defaults_use_unambiguous_number_tokens() -> None:
    output = AppConfig().output

    assert output.directory_format == "%CHAPTER_NUMBER%_%CONTENT_TITLE%_%CHAPTER_TITLE%"
    assert output.filename_format == "%IMAGE_INDEX%.%EXT%"


def test_allocator_distinguishes_chapter_number_from_image_index(tmp_path: Path) -> None:
    async def scenario() -> None:
        filesystem = FileSystem(tmp_path.resolve())
        config = AppConfig.model_validate(
            {
                "output": {
                    "directory_format": "%CHAPTER_NUMBER%_%CHAPTER_TITLE%_%EXT%",
                    "filename_format": "%CHAPTER_NUMBER%_%IMAGE_INDEX%.%EXT%",
                }
            }
        )
        allocator = OutputAllocator(filesystem, config)
        chapter = Chapter(12, "chapter")
        directory = _directory(allocator, chapter)
        filesystem.ensure_directory(directory)

        allocation = await _allocate(
            allocator, directory, ImageResource("https://example.test/3", index=3), chapter, ".jpeg"
        )

        assert directory.name == "0012_chapter_jpeg"
        assert allocation.relative_path == Path("0012_chapter_jpeg/0012_0003.jpeg")
        await allocation.abort()

    asyncio.run(scenario())


def test_allocator_keeps_plugin_provided_zero_numbers(tmp_path: Path) -> None:
    async def scenario() -> None:
        filesystem = FileSystem(tmp_path.resolve())
        allocator = OutputAllocator(filesystem, AppConfig())
        chapter = Chapter(0, "chapter")
        directory = _directory(allocator, chapter)
        filesystem.ensure_directory(directory)

        allocation = await _allocate(
            allocator, directory, ImageResource("https://example.test/0", index=0), chapter, ".jpeg"
        )

        assert directory.name == "0000_content_chapter"
        assert allocation.relative_path.name == "0000.jpeg"
        await allocation.abort()

    asyncio.run(scenario())


@pytest.mark.parametrize("field", ("directory_format", "filename_format"))
@pytest.mark.parametrize("token", ("%NUM%", "%TITLE%", "%SUBTITLE%"))
def test_legacy_format_tokens_are_rejected(field: str, token: str) -> None:
    with pytest.raises(ValueError, match=rf"cannot contain {token}"):
        AppConfig.model_validate({"output": {field: token}})


@pytest.mark.parametrize(
    "template",
    (
        "%PLUGIN[com.example.gray:filter_name]%",
        "%PLUGIN[com.example.gray:FILTER-NAME]%",
        "%PLUGIN[not-a-plugin:FILTER_NAME]%",
        "%PLUGIN[com.example.gray:FILTER_NAME]",
    ),
)
def test_invalid_plugin_tokens_are_rejected(template: str) -> None:
    with pytest.raises(ValueError, match=r"invalid %PLUGIN"):
        AppConfig.model_validate({"output": {"filename_format": template}})


def test_allocator_expands_core_and_plugin_tokens_without_recursion(tmp_path: Path) -> None:
    async def scenario() -> None:
        filesystem = FileSystem(tmp_path.resolve())
        config = AppConfig.model_validate(
            {
                "output": {
                    "directory_format": (
                        "%CHAPTER_NUMBER%_%CONTENT_TITLE%_%CHAPTER_TITLE%_"
                        "%CHAPTER_SUBTITLE%_%PLUGIN[com.example.gray:FILTER_NAME]%"
                    ),
                    "filename_format": (
                        "%CHAPTER_NUMBER%_%IMAGE_INDEX%_%CONTENT_TITLE%_"
                        "%CHAPTER_TITLE%_%CHAPTER_SUBTITLE%_"
                        "%PLUGIN[com.example.gray:FILTER_NAME]%.%EXT%"
                    ),
                }
            }
        )
        allocator = OutputAllocator(filesystem, config)
        chapter = Chapter(12, "Chapter", "Subtitle")
        image = ImageResource("image:3", index=3)
        context = OutputFormatContext(
            DownloadManifest("Content", (chapter,)),
            chapter,
            "https://example.test/content",
            "com.example.site",
            image,
            ".jpeg",
            plugin_values={"com.example.gray": {"FILTER_NAME": "_grayscale%CONTENT_TITLE%"}},
        )
        directory = allocator.chapter_directory(context)
        filesystem.ensure_directory(directory)
        allocation = await allocator.allocate(directory, context)

        assert directory.name == "0012_Content_Chapter_Subtitle_grayscale%CONTENT_TITLE%"
        assert allocation.relative_path.name == "0012_0003_Content_Chapter_Subtitle_grayscale%CONTENT_TITLE%.jpeg"
        await allocation.abort()

    asyncio.run(scenario())


def test_missing_plugin_token_remains_literal_and_context_values_are_frozen(tmp_path: Path) -> None:
    async def scenario() -> None:
        filesystem = FileSystem(tmp_path.resolve())
        config = AppConfig.model_validate(
            {"output": {"filename_format": "%PLUGIN[com.example.absent:FILTER_NAME]%.%EXT%"}}
        )
        allocator = OutputAllocator(filesystem, config)
        chapter = Chapter(1, "Chapter")
        context = _context(
            chapter,
            ImageResource("image:1"),
            ".jpeg",
            plugin_values={"com.example.absent": {"OTHER_KEY": "value"}},
        )
        directory = _directory(allocator, chapter)
        filesystem.ensure_directory(directory)
        allocation = await allocator.allocate(directory, context)

        assert allocation.relative_path.name == "%PLUGIN[com．example．absent：FILTER_NAME]%.jpeg"
        with pytest.raises(TypeError):
            context.plugin_values["com.example.other"] = {}  # type: ignore[index]
        with pytest.raises(TypeError):
            context.plugin_values["com.example.absent"]["FILTER_NAME"] = "unexpected"  # type: ignore[index]
        await allocation.abort()

    asyncio.run(scenario())


def test_empty_and_unsafe_plugin_values_are_safely_expanded(tmp_path: Path) -> None:
    async def scenario() -> None:
        filesystem = FileSystem(tmp_path.resolve())
        config = AppConfig.model_validate(
            {
                "output": {
                    "directory_format": "chapter%PLUGIN[com.example.plugin:EMPTY]%",
                    "filename_format": "image%PLUGIN[com.example.plugin:UNSAFE]%.%EXT%",
                }
            }
        )
        allocator = OutputAllocator(filesystem, config)
        chapter = Chapter(1, "Chapter")
        context = _context(
            chapter,
            ImageResource("image:1"),
            ".jpeg",
            plugin_values={"com.example.plugin": {"EMPTY": "", "UNSAFE": "../unsafe\\name:<value>"}},
        )
        directory = allocator.chapter_directory(context)
        filesystem.ensure_directory(directory)
        allocation = await allocator.allocate(directory, context)

        assert directory.name == "chapter"
        assert allocation.relative_path.parent == Path("chapter")
        assert "/" not in allocation.relative_path.name
        assert "\\" not in allocation.relative_path.name
        assert allocation.relative_path.name not in {".", ".."}
        await allocation.abort()

    asyncio.run(scenario())


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

        allocation = await _allocate(
            allocator, directory, ImageResource("https://example.test/1"), Chapter(1, "one"), ".jpeg"
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
            await _allocate(allocator, directory, ImageResource("https://example.test/1"), Chapter(1, "one"), ".jpeg")
        assert raised.value.code == "existing_file_conflict"
        assert raised.value.reason == "output file already exists and existing-file=error prevents overwrite"
        assert raised.value.relative_path == directory / "0001.jpeg"
        assert raised.value.policy == "error"

    asyncio.run(scenario())


def test_skip_allocation_has_no_terminal_reservation(tmp_path: Path) -> None:
    async def scenario() -> None:
        filesystem, allocator, directory = _allocator(tmp_path, _config("skip"))
        filesystem.write_bytes_atomic(directory / "0001.jpeg", b"existing")
        skipped = await _allocate(
            allocator, directory, ImageResource("https://example.test/1"), Chapter(1, "one"), ".jpeg"
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
        reserved = await _allocate(
            allocator, directory, ImageResource("https://example.test/1"), Chapter(1, "one"), ".jpeg"
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
            allocation = await _allocate(
                allocator, directory, ImageResource("https://example.test/duplicate"), Chapter(1, "one"), ".jpeg"
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
        first = await _allocate(
            rename_allocator, rename_directory, ImageResource("https://example.test/1"), Chapter(1, "one"), ".jpeg"
        )
        second = await _allocate(
            rename_allocator, rename_directory, ImageResource("https://example.test/2"), Chapter(1, "one"), ".jpeg"
        )
        assert second.relative_path.name == "0001_1.jpeg"
        await first.abort()
        await second.abort()

        _, error_allocator, error_directory = _allocator(tmp_path / "error", _config("error"))
        reserved = await _allocate(
            error_allocator, error_directory, ImageResource("https://example.test/1"), Chapter(1, "one"), ".jpeg"
        )
        with pytest.raises(ExistingFileConflictError) as raised:
            await _allocate(
                error_allocator, error_directory, ImageResource("https://example.test/2"), Chapter(1, "one"), ".jpeg"
            )
        assert raised.value.relative_path == error_directory / "0001.jpeg"
        assert raised.value.policy == "error"
        await reserved.abort()

    asyncio.run(scenario())


def test_skip_waits_for_commit_and_returns_the_committed_path(tmp_path: Path) -> None:
    async def scenario() -> None:
        filesystem, allocator, directory = _allocator(tmp_path, _config("skip"))
        first = await _allocate(
            allocator, directory, ImageResource("https://example.test/1"), Chapter(1, "one"), ".jpeg"
        )
        waiting = asyncio.create_task(
            _allocate(allocator, directory, ImageResource("https://example.test/2"), Chapter(1, "one"), ".jpeg")
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
        first = await _allocate(
            allocator, directory, ImageResource("https://example.test/1"), Chapter(1, "one"), ".jpeg"
        )
        waiting = asyncio.create_task(
            _allocate(allocator, directory, ImageResource("https://example.test/2"), Chapter(1, "one"), ".jpeg")
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
        filesystem, allocator, directory = _allocator(
            tmp_path, _config("skip", filename_format="%CHAPTER_TITLE%.%EXT%")
        )
        existing_name = "Caf\N{LATIN SMALL LETTER E WITH ACUTE}.jpeg"
        filesystem.write_bytes_atomic(directory / existing_name, b"existing")
        requested_title = unicodedata.normalize("NFD", "CAF\N{LATIN SMALL LETTER E WITH ACUTE}")

        allocation = await _allocate(
            allocator, directory, ImageResource("https://example.test/1"), Chapter(1, requested_title), ".jpeg"
        )

        assert not allocation.should_write
        assert allocation.relative_path.name == existing_name

    asyncio.run(scenario())


def test_collision_suffix_respects_optional_component_limit(tmp_path: Path) -> None:
    async def scenario() -> None:
        _, allocator, directory = _allocator(
            tmp_path,
            _config("overwrite", filename_format="%CHAPTER_TITLE%.%EXT%", max_length=16),
        )
        chapter = Chapter(1, "abcdefghijk")
        first = await _allocate(allocator, directory, ImageResource("https://example.test/1"), chapter, ".jpeg")
        second = await _allocate(allocator, directory, ImageResource("https://example.test/2"), chapter, ".jpeg")

        assert first.relative_path != second.relative_path
        assert len(second.relative_path.name) <= 16
        assert second.relative_path.name.endswith("_1.jpeg")
        await first.abort()
        await second.abort()

    asyncio.run(scenario())
