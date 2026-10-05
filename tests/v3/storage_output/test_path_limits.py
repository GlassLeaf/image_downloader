from __future__ import annotations

import asyncio
import errno
import hashlib
import os
from pathlib import Path

import pytest

from image_downloader.configuration.models import AppConfig
from image_downloader.exceptions import StorageError, error_info_for
from image_downloader.models import Chapter, DownloadManifest, ImageResource
from image_downloader.output.output_allocator import OutputAllocator, OutputFormatContext
from image_downloader.storage import FileSystem, atomic_write, safe_component
from image_downloader.storage import _path_limits as limits


@pytest.mark.parametrize(
    "windows,value,tail",
    [
        (True, "😀" * 200 + ".png", ".png"),
        (False, "日" * 200 + ".png", ".png"),
        (True, "a" * 255 + "_9999.jpeg", "_9999.jpeg"),
    ],
)
def test_native_shortening_retains_suffix_and_identity(windows: bool, value: str, tail: str) -> None:
    budget = limits._ComponentLimit(255, windows)
    result = budget.shorten(value, tail=tail)
    assert budget.measure(result) <= 255
    assert result.endswith("_" + hashlib.sha256(value.encode()).hexdigest()[:8] + tail)
    assert result == budget.shorten(value, tail=tail)
    assert result != budget.shorten(value.replace(value[0], "b", 1), tail=tail)


@pytest.mark.parametrize("windows,value", [(True, "a" * 255), (True, "日" * 200), (False, "a" * 255)])
def test_native_boundary_does_not_change_valid_names(windows: bool, value: str) -> None:
    assert limits._ComponentLimit(255, windows).shorten(value) == value


def test_required_suffix_cannot_be_silently_lost() -> None:
    with pytest.raises(StorageError) as caught:
        limits._ComponentLimit(12, False).shorten("a" * 40 + ".jpeg", tail=".jpeg")
    assert "filesystem limit" in error_info_for(caught.value).message


def test_native_limit_is_cached_per_allocator_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import image_downloader.output.output_allocator as module

    calls = []

    def query(parent: Path) -> limits._ComponentLimit:
        calls.append(parent)
        return limits._ComponentLimit(255, True)

    monkeypatch.setattr(module, "_component_limit", query)
    allocator = OutputAllocator(FileSystem(tmp_path), AppConfig())
    assert allocator._fit_native("日本語", tmp_path) == "日本語"
    assert allocator._fit_native("ordinary", tmp_path) == "ordinary"
    assert calls == [tmp_path]


def test_native_too_long_diagnosis_distinguishes_component_from_total(tmp_path: Path) -> None:
    error = OSError(errno.ENAMETOOLONG, "secret path")
    component = limits._path_diagnostic(tmp_path / ("a" * 256), error, creating=True)
    total = limits._path_diagnostic(tmp_path / "file", error, creating=True)
    assert component is not None and total is not None
    assert "filesystem limit" in error_info_for(component).message
    assert "path length limit" in error_info_for(total).message
    assert "secret path" not in error_info_for(total).message


def test_temporary_creation_failure_leaves_existing_file_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import image_downloader.storage.filesystem as module

    fs = FileSystem(tmp_path)
    fs.write_bytes_atomic("file.png", b"original")

    def fail(**kwargs: object) -> tuple[int, str]:
        raise PermissionError("denied")

    monkeypatch.setattr(module.tempfile, "mkstemp", fail)
    for writer in (
        lambda: fs.write_bytes_atomic("file.png", b"new"),
        lambda: atomic_write(tmp_path / "file.png", b"new"),
    ):
        with pytest.raises(PermissionError):
            writer()
        assert (tmp_path / "file.png").read_bytes() == b"original"
        assert len(list(tmp_path.iterdir())) == 1


def test_nearest_existing_parent_and_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert limits._nearest_parent(tmp_path / "missing" / "nested") == tmp_path
    function = "_windows_component_limit" if os.name == "nt" else "_unused"
    if os.name == "nt":
        monkeypatch.setattr(limits, function, lambda parent: -1)
    else:
        monkeypatch.setattr(os, "pathconf", lambda parent, key: -1)
    assert limits._component_limit(tmp_path / "missing").maximum == 255

    def unavailable(*args: object) -> int:
        raise OSError("unavailable")

    if os.name == "nt":
        monkeypatch.setattr(limits, function, unavailable)
    else:
        monkeypatch.setattr(os, "pathconf", unavailable)
    assert limits._component_limit(tmp_path).maximum == 255


def test_allocator_preserves_legacy_and_fits_only_overflow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import image_downloader.output.output_allocator as module

    monkeypatch.setattr(module, "_component_limit", lambda parent: limits._ComponentLimit(255, True))
    config = AppConfig.model_validate(
        {"output": {"max_component_length": 16, "filename_format": "%CHAPTER_TITLE%.%EXT%"}}
    )
    chapter = Chapter(1, "日" * 20)
    context = OutputFormatContext(
        DownloadManifest(chapter.title, (chapter,)),
        chapter,
        "https://example.test/",
        "core.generic-html",
        ImageResource("https://example.test/1"),
        ".png",
    )
    allocator = OutputAllocator(FileSystem(tmp_path), config)
    assert allocator._format_filename(config.output.filename_format, context, original_filename=None) == safe_component(
        chapter.title + ".png", max_length=16
    )
    base = Path("a" * 11 + ".jpeg")
    assert allocator._renamed_candidate(base, 1).name == hashlib.sha256(base.name.encode()).hexdigest()[:8] + "_1.jpeg"
    chapter = Chapter(1, "日" * 125)
    context = OutputFormatContext(
        DownloadManifest(chapter.title, (chapter,)), chapter, "https://example.test/", "core.generic-html"
    )
    allocator = OutputAllocator(FileSystem(tmp_path), AppConfig())
    directory = allocator.chapter_directory(context)
    assert directory.name != "0001_" + chapter.title + "_" + chapter.title
    assert limits._ComponentLimit(255, True).measure(directory.name) <= 255
    renamed = allocator._renamed_candidate(Path("😀" * 125 + ".png"), 1)
    assert renamed.name.endswith("_1.png")
    assert limits._ComponentLimit(255, True).measure(renamed.name) <= 255


@pytest.mark.parametrize("mode", ["overwrite", "skip", "rename", "error"])
def test_shortened_names_keep_existing_file_policy(tmp_path: Path, mode: str) -> None:
    from image_downloader.exceptions import ExistingFileConflictError

    async def scenario() -> None:
        config = AppConfig.model_validate(
            {"output": {"existing_file": mode, "filename_format": "%CHAPTER_TITLE%.%EXT%"}}
        )
        fs = FileSystem(tmp_path)
        allocator = OutputAllocator(fs, config)
        fs.ensure_directory("chapter")
        chapter = Chapter(1, "😀" * 300)
        context = OutputFormatContext(
            DownloadManifest("content", (chapter,)),
            chapter,
            "https://example.test/",
            "test.site",
            ImageResource("https://example.test/1.png"),
            ".png",
        )
        first = await allocator.allocate(Path("chapter"), context)
        fs.write_bytes_atomic(first.relative_path, b"original")
        await first.commit()
        other = OutputAllocator(fs, config)
        if mode == "error":
            with pytest.raises(ExistingFileConflictError):
                await other.allocate(Path("chapter"), context)
        else:
            second = await other.allocate(Path("chapter"), context)
            assert (second.relative_path == first.relative_path) == (mode != "rename")
            assert second.should_write == (mode != "skip")
            if second.should_write:
                await second.abort()
        assert fs.read_bytes_bounded(first.relative_path, 8) == b"original"

    asyncio.run(scenario())


@pytest.mark.parametrize("length", [245, 246, 255])
@pytest.mark.parametrize("unconfined", [False, True])
def test_atomic_long_leaf_roundtrip(tmp_path: Path, length: int, unconfined: bool) -> None:
    name = "a" * (length - 4) + ".png"
    fs = FileSystem(tmp_path)
    for payload in (b"original", b"updated\x00\xff"):
        if unconfined:
            atomic_write(tmp_path / name, payload)
        else:
            fs.write_bytes_atomic(name, payload)
        assert fs.read_bytes_bounded(name, len(payload)) == payload
    assert [path.name for path in tmp_path.iterdir()] == [name]


def test_atomic_replace_failure_preserves_existing_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import image_downloader.storage.filesystem as module

    fs = FileSystem(tmp_path)
    fs.write_bytes_atomic("file.png", b"original")

    def fail(*args: object) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(module.os, "replace", fail)
    for writer in (
        lambda: fs.write_bytes_atomic("file.png", b"new"),
        lambda: atomic_write(tmp_path / "file.png", b"new"),
    ):
        with pytest.raises(PermissionError):
            writer()
        assert (tmp_path / "file.png").read_bytes() == b"original"
        assert [path.name for path in tmp_path.iterdir()] == ["file.png"]


def test_component_diagnosis_does_not_rename_explicit_paths(tmp_path: Path) -> None:
    fs = FileSystem(tmp_path)
    with pytest.raises(StorageError) as caught:
        fs.write_bytes_atomic("a" * 256, b"new")
    info = error_info_for(caught.value)
    assert info.code == "storage_error"
    assert info.reason == "storage operation failed"
    assert str(tmp_path) not in info.message
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError("missing"),
        PermissionError("denied"),
        ValueError("unrelated"),
        OSError(errno.EINVAL, "invalid"),
    ],
)
def test_unrelated_errors_and_missing_files_are_unchanged(tmp_path: Path, error: OSError | ValueError) -> None:
    assert limits._path_diagnostic(tmp_path / "file", error, creating=True) is None
    assert limits._path_diagnostic(tmp_path / "file", error, creating=False) is None
    assert not FileSystem(tmp_path).exists("missing")


def test_safe_diagnostics_reject_arbitrary_marker_text() -> None:
    error = StorageError("secret text")
    error.__dict__["_path_length_diagnostic"] = "secret marker"
    assert error_info_for(error).message == "storage operation failed"


@pytest.mark.skipif(os.name != "nt", reason="Windows-specific error reporting")
def test_windows_long_creation_and_python_guard(tmp_path: Path) -> None:
    parent = tmp_path / ("a" * 180)
    parent.mkdir()
    path = parent / ("f" * 100)
    diagnosed = limits._path_diagnostic(path, FileNotFoundError("missing"), creating=True)
    assert diagnosed is not None
    assert "may be" in error_info_for(diagnosed).message
    assert limits._path_diagnostic(path, FileNotFoundError("missing"), creating=False) is None
    diagnosed = limits._path_diagnostic(path, ValueError("open: path too long for Windows"), creating=True)
    assert diagnosed is not None
    assert "path length limit" in error_info_for(diagnosed).message
