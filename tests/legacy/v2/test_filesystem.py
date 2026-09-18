from __future__ import annotations

from pathlib import Path

import pytest

from image_downloader.application.downloader import _format_name
from image_downloader.exceptions import StorageSafetyError
from image_downloader.storage import FileSystem, safe_component


def test_filesystem_confines_paths_and_supports_atomic_write_and_append(tmp_path: Path) -> None:
    filesystem = FileSystem(tmp_path / "root")
    path = filesystem.write_bytes_atomic(Path("chapter") / "image.jpeg", b"image")
    assert path == tmp_path / "root" / "chapter" / "image.jpeg"
    assert filesystem.read_text(Path("chapter") / "image.jpeg", encoding="utf-8") == "image"
    with filesystem.open_text_append(Path("chapter") / "log.log") as stream:
        stream.write("entry\n")
    assert filesystem.read_text(Path("chapter") / "log.log") == "entry\n"
    for unsafe in (Path("..") / "escape", Path("/absolute"), Path("CON.txt")):
        with pytest.raises(StorageSafetyError):
            filesystem.write_bytes_atomic(unsafe, b"blocked")


def test_filesystem_rejects_symlink_ancestor_and_leaf(tmp_path: Path) -> None:
    filesystem = FileSystem(tmp_path / "root")
    filesystem.ensure_directory()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "root" / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links are unavailable in this environment")
    with pytest.raises(StorageSafetyError):
        filesystem.write_bytes_atomic(Path("linked") / "image.jpeg", b"blocked")
    leaf = tmp_path / "root" / "leaf.txt"
    leaf.symlink_to(outside / "target.txt")
    with pytest.raises(StorageSafetyError):
        filesystem.open_text_append("leaf.txt")


def test_template_names_preserve_normal_values_without_adding_hash_suffixes() -> None:
    assert _format_name("%NUM%_%TITLE%_%SUBTITLE%", 1, "Title", "chapter1") == "0001_Title_chapter1"
    assert _format_name("%NUM%_%TITLE%_%SUBTITLE%", 1, "Title", "") == "0001_Title"
    assert _format_name("%NUM%-%TITLE%-%SUBTITLE%", 1, "Title", "") == "0001-Title"
    slash = _format_name("%TITLE%", 1, "A/B", "")
    backslash = _format_name("%TITLE%", 1, "A\\B", "")
    assert slash == "A_B"
    assert backslash == "A_B"
    assert _format_name("%TITLE%", 1, "CON.txt", "") == "_CON.txt"
    assert safe_component("A/B") == "A_B"
