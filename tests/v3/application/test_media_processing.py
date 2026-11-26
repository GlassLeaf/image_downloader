"""Image processor normalization and option validation."""

from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace

import pytest
from PIL import Image

from image_downloader.config import AppConfig
from image_downloader.exceptions import (
    ConfigurationError,
    ImageContentTypeError,
    ImageDecodeError,
    ImageMimeMismatchError,
    UnsupportedImageFormatError,
)
from image_downloader.media import ImageProcessor
from image_downloader.media.artifact_pipeline import ArtifactPipeline
from image_downloader.media.image_processor import ImageEncodingOptions
from image_downloader.media.pipeline_binding import PreparedPipelineBinding
from image_downloader.models import Chapter, DownloadManifest, ImageArtifact, ImageResource


def _image(image_format: str = "PNG", size: tuple[int, int] = (8, 6)) -> bytes:
    output = io.BytesIO()
    Image.new("RGBA", size, "red").save(output, format=image_format)
    return output.getvalue()


def _binding(config=None):
    return PreparedPipelineBinding.prepare(config or AppConfig(), "com.example.site", {}, {}, {}, None)


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
            options=ImageEncodingOptions(format="JPEG", quality=80, optimize=True, progressive=True),
        )
        assert extension == ".jpeg"
        with Image.open(io.BytesIO(converted)) as image:
            assert image.format == "JPEG"


@pytest.mark.parametrize("target", ["JPEG", "PNG", "WEBP", "TIFF"])
@pytest.mark.parametrize("preserve_exif", [False, True])
def test_reencoding_preserves_requested_exif_or_applies_orientation_before_removal(target, preserve_exif) -> None:
    source = io.BytesIO()
    exif = Image.Exif()
    exif[274] = 6  # Rotate 90 degrees clockwise for display.
    exif[315] = "sample photographer"
    original = Image.new("RGB", (8, 6), "red")
    original.paste("blue", (0, 0, 4, 6))
    original.save(source, format="JPEG", quality=100, exif=exif)

    with ImageProcessor() as processor:
        data, _extension = processor.process(
            source.getvalue(),
            source_url="https://example.test/photo.jpg",
            content_type="image/jpeg",
            options=ImageEncodingOptions(format=target, exif=preserve_exif),
        )
    with Image.open(io.BytesIO(data)) as decoded:
        metadata = decoded.getexif()
        if preserve_exif:
            assert metadata[274] == 6
            assert metadata[315] == "sample photographer"
        else:
            # TIFF also exposes required structural tags through getexif().
            assert 274 not in metadata and 315 not in metadata
            if target != "TIFF":
                assert not metadata
            assert decoded.size == (6, 8)
            # Orientation must change pixels, not only the reported dimensions.
            top = decoded.convert("RGB").getpixel((3, 1))
            bottom = decoded.convert("RGB").getpixel((3, 6))
            assert top[2] > top[0] and bottom[0] > bottom[2]


def test_reencoding_rejects_exif_preservation_for_an_unsupported_encoder() -> None:
    with ImageProcessor() as processor, pytest.raises(ConfigurationError, match="EXIF preservation"):
        processor.process(
            _image(),
            source_url="https://example.test/image.png",
            content_type="image/png",
            options=ImageEncodingOptions(format="GIF", exif=True),
        )


@pytest.mark.parametrize(
    ("options", "message"),
    [
        (ImageEncodingOptions(format="unknown"), "unsupported image format"),
        (ImageEncodingOptions(quality=101), "quality"),
        (ImageEncodingOptions(compress_level=10), "compress_level"),
        (ImageEncodingOptions(format="PNG", extension=".jpg"), "extension must match"),
    ],
)
def test_image_processor_rejects_invalid_save_options(options: ImageEncodingOptions, message: str) -> None:
    with ImageProcessor() as processor, pytest.raises(ConfigurationError, match=message):
        processor.process(
            _image(), source_url="https://example.test/image.png", content_type="image/png", options=options
        )


def test_image_processor_rejects_invalid_bytes() -> None:
    with ImageProcessor() as processor, pytest.raises(ImageDecodeError, match="invalid image data"):
        processor.inspect(b"not an image")


def test_image_processor_reports_unsupported_decoded_format() -> None:
    with (
        ImageProcessor() as processor,
        pytest.raises(UnsupportedImageFormatError, match="unsupported image format: ICO"),
    ):
        processor.process(
            _image("ICO", (16, 16)),
            source_url="https://example.test/icon.ico",
            content_type="image/x-icon",
        )


def test_input_validation_uses_specific_content_type_and_mime_errors() -> None:
    pipeline = ArtifactPipeline.__new__(ArtifactPipeline)
    pipeline.binding = _binding(AppConfig.model_validate({"media": {"input_validation": "content_type"}}))
    with pytest.raises(ImageContentTypeError, match="non-image content type"):
        pipeline._validate_input(ImageArtifact(b"<html>", "text/html", "https://example.test/error"))

    processor = ImageProcessor()
    pipeline.binding = _binding(
        AppConfig.model_validate({"media": {"input_validation": "decode", "content_type_mismatch": "error"}})
    )
    pipeline.core = processor
    try:
        with pytest.raises(ImageMimeMismatchError, match="MIME"):
            pipeline._validate_input(ImageArtifact(_image(), "image/jpeg", "https://example.test/image"))
    finally:
        processor.close()


def test_original_format_preserves_the_transformed_artifact_and_validates_it_once() -> None:
    transformed = ImageArtifact(b"unconverted-avif", "image/avif", "image:1")
    validations: list[ImageArtifact] = []

    class Invoker:
        async def transform_image(self, *_args: object, **_kwargs: object) -> ImageArtifact:
            return transformed

        async def image_save_options(self, *_args: object, **_kwargs: object):
            return None

    class Logger:
        async def core(self, *_args: object, **_kwargs: object) -> None:
            return None

    pipeline = ArtifactPipeline.__new__(ArtifactPipeline)
    pipeline.binding = _binding()
    pipeline.core = SimpleNamespace()
    pipeline.logger = Logger()
    pipeline.invoker = Invoker()
    pipeline.processors = ()
    pipeline.image_format = None
    pipeline.image_quality = None
    pipeline.transform_executor = None
    pipeline._validate_input = validations.append

    image = ImageResource("image:1")
    result = asyncio.run(
        pipeline.process(
            object(),
            ImageArtifact(b"source", "image/png", image.url),
            image,
            DownloadManifest("gallery", (Chapter(1, "chapter", images=(image,)),)),
            Chapter(1, "chapter", images=(image,)),
        )
    )

    assert result.data == transformed.data
    assert result.extension == ".avif"
    assert result.history[-1] == "core.original-preserve"
    assert validations == [transformed]
