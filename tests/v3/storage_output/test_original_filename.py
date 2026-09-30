from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from image_downloader.config import AppConfig
from image_downloader.models import Chapter, ImageResource, RequestResponse
from image_downloader.output.original_filename import original_filename_parts, resolve_original_filename
from image_downloader.runtime import OutputAllocator
from image_downloader.storage import FileSystem


@pytest.mark.parametrize(
    ("image", "response", "expected"),
    (
        (
            ImageResource("https://source.test/locator.webp", original_filename="plugin-name.avif"),
            RequestResponse(
                "https://cdn.test/final.webp",
                200,
                {"Content-Disposition": "attachment; filename=header.webp"},
                b"",
            ),
            "plugin-name.avif",
        ),
        (
            ImageResource("https://source.test/locator.webp"),
            RequestResponse(
                "https://cdn.test/final.webp",
                200,
                {"Content-Disposition": "attachment; filename=header.webp"},
                b"",
            ),
            "header.webp",
        ),
        (
            ImageResource("https://source.test/locator.webp"),
            RequestResponse("https://cdn.test/final.webp?signature=secret#ignored", 200, {}, b""),
            "final.webp",
        ),
        (
            ImageResource("https://source.test/locator%20name.webp?token=secret"),
            RequestResponse("https://cdn.test/", 200, {}, b""),
            "locator name.webp",
        ),
        (
            ImageResource("image:42", index=7),
            RequestResponse("https://cdn.test/", 200, {}, b""),
            "0007",
        ),
    ),
)
def test_resolve_original_filename_uses_the_documented_priority(
    image: ImageResource, response: RequestResponse, expected: str
) -> None:
    assert resolve_original_filename(image, response) == expected


def test_content_disposition_prefers_utf8_filename_star_and_recovers_to_filename() -> None:
    image = ImageResource("image:42")
    preferred = RequestResponse(
        "https://cdn.test/final.webp",
        200,
        {"cOnTeNt-DisPoSiTiOn": "attachment; filename=plain.webp; filename*=UTF-8''caf%C3%A9.webp"},
        b"",
    )
    malformed_star = RequestResponse(
        "https://cdn.test/final.webp",
        200,
        {"content-disposition": "attachment; filename=fallback.webp; filename*=UTF-8''bad%ZZ"},
        b"",
    )

    assert resolve_original_filename(image, preferred) == "café.webp"
    assert resolve_original_filename(image, malformed_star) == "fallback.webp"


def test_invalid_header_and_source_path_components_do_not_escape_the_filename_boundary() -> None:
    image = ImageResource("https://source.test/../../locator.webp")
    invalid_header = RequestResponse(
        "https://cdn.test/final.webp",
        200,
        {"content-disposition": "attachment; filename=one.webp; filename=two.webp"},
        b"",
    )
    path_header = RequestResponse(
        "https://cdn.test/final.webp",
        200,
        {"content-disposition": 'attachment; filename="../../header.webp"'},
        b"",
    )

    assert resolve_original_filename(image, invalid_header) == "final.webp"
    assert resolve_original_filename(image, path_header) == "header.webp"


@pytest.mark.parametrize(
    ("filename", "index", "expected"),
    (
        ("original_file.webp", 1, ("original_file.webp", "original_file", "webp")),
        ("archive.tar.webp", 1, ("archive.tar.webp", "archive.tar", "webp")),
        ("cover", 1, ("cover", "cover", "")),
        (None, 7, ("0007", "0007", "")),
    ),
)
def test_original_filename_parts_do_not_infer_a_missing_extension(
    filename: str | None, index: int, expected: tuple[str, str, str]
) -> None:
    assert original_filename_parts(filename, index) == expected


def test_output_allocator_expands_original_filename_tokens_and_keeps_final_extension(tmp_path: Path) -> None:
    async def scenario() -> None:
        filesystem = FileSystem(tmp_path.resolve())
        directory = Path("chapter")
        filesystem.ensure_directory(directory)
        config = AppConfig.model_validate(
            {"output": {"filename_format": "%ORIGINAL_STEM%.%EXT%", "existing_file": "overwrite"}}
        )
        allocator = OutputAllocator(filesystem, config)
        allocation = await allocator.allocate(
            directory,
            ImageResource("image:1", original_filename="archive.tar.webp"),
            Chapter(1, "chapter"),
            ".jpeg",
        )
        assert allocation.relative_path.name == "archive.tar.jpeg"
        await allocation.abort()

        image = ImageResource("image:format", index=1)
        chapter = Chapter(1, "")
        assert (
            allocator._format_filename(
                "%ORIGINAL_FILENAME%",
                image,
                chapter,
                extension=".jpeg",
                original_filename="archive.tar.webp",
            )
            == "archive.tar.webp"
        )
        assert (
            allocator._format_filename(
                "%ORIGINAL_EXT%",
                image,
                chapter,
                extension=".jpeg",
                original_filename="archive.tar.webp",
            )
            == "webp"
        )
        assert (
            allocator._format_filename(
                "%ORIGINAL_EXT%",
                image,
                chapter,
                extension=".jpeg",
                original_filename="cover",
            )
            == "download"
        )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "token", ("%IMAGE_INDEX%", "%ORIGINAL_STEM%", "%ORIGINAL_FILENAME%", "%ORIGINAL_EXT%")
)
def test_image_only_tokens_are_rejected_in_directory_format(token: str) -> None:
    with pytest.raises(ValueError, match="directory_format cannot contain image-only tokens"):
        AppConfig.model_validate({"output": {"directory_format": f"chapter_{token}"}})
