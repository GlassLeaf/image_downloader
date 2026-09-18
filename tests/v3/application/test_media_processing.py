"""Image processor normalization and option validation."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from image_downloader.exceptions import ConfigurationError, DownloaderError
from image_downloader.media import ImageProcessor
from image_downloader.models import ImageSaveOptions


def _image(image_format: str = "PNG", size: tuple[int, int] = (8, 6)) -> bytes:
    output = io.BytesIO()
    Image.new("RGBA", size, "red").save(output, format=image_format)
    return output.getvalue()


def test_image_processor_preserves_matching_data_and_converts_when_requested() -> None:
    with ImageProcessor() as processor:
        source = _image()
        preserved, extension = processor.process(
            source, source_url="https://example.test/image.png", content_type="image/png"
        )
        assert preserved == source
        assert extension == ".png"

        converted, extension = processor.process(
            source,
            source_url="https://example.test/image.bin",
            content_type="application/octet-stream",
            options=ImageSaveOptions(format="JPEG", quality=80, optimize=True, progressive=True),
        )
        assert extension == ".jpeg"
        with Image.open(io.BytesIO(converted)) as image:
            assert image.format == "JPEG"


@pytest.mark.parametrize(
    ("options", "message"),
    [
        (ImageSaveOptions(format="unknown"), "unsupported image format"),
        (ImageSaveOptions(quality=101), "quality"),
        (ImageSaveOptions(compress_level=10), "compress_level"),
        (ImageSaveOptions(format="PNG", extension=".jpg"), "extension must match"),
    ],
)
def test_image_processor_rejects_invalid_save_options(options: ImageSaveOptions, message: str) -> None:
    with ImageProcessor() as processor, pytest.raises(ConfigurationError, match=message):
        processor.process(
            _image(), source_url="https://example.test/image.png", content_type="image/png", options=options
        )


def test_image_processor_rejects_invalid_bytes() -> None:
    with ImageProcessor() as processor, pytest.raises(DownloaderError, match="invalid image data"):
        processor.inspect(b"not an image")
