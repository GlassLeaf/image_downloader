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
from image_downloader.models import Chapter, DownloadManifest, ImageArtifact, ImageResource, ImageSaveOptions


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
            options=ImageSaveOptions(format=target, exif=preserve_exif),
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
            options=ImageSaveOptions(format="GIF", exif=True),
        )


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
    pipeline.config = AppConfig.model_validate({"media": {"input_validation": "content_type"}})
    with pytest.raises(ImageContentTypeError, match="non-image content type"):
        pipeline._validate_input(ImageArtifact(b"<html>", "text/html", "https://example.test/error"))

    processor = ImageProcessor()
    pipeline.config = AppConfig.model_validate(
        {"media": {"input_validation": "decode", "content_type_mismatch": "error"}}
    )
    pipeline.core = processor
    try:
        with pytest.raises(ImageMimeMismatchError, match="MIME"):
            pipeline._validate_input(ImageArtifact(_image(), "image/jpeg", "https://example.test/image"))
    finally:
        processor.close()


def test_original_format_preserves_the_transformed_artifact_and_validates_it_twice() -> None:
    transformed = ImageArtifact(b"unconverted-avif", "image/avif", "image:1")
    validations: list[ImageArtifact] = []

    class Invoker:
        async def transform_image(self, *_args: object, **_kwargs: object) -> ImageArtifact:
            return transformed

    class Registry:
        @staticmethod
        def effective_config(*_args: object) -> dict[str, object]:
            return {}

    class Logger:
        async def core(self, *_args: object, **_kwargs: object) -> None:
            return None

    pipeline = ArtifactPipeline.__new__(ArtifactPipeline)
    pipeline.config = AppConfig()
    pipeline.registry = Registry()
    pipeline.site_record = SimpleNamespace(id="com.example.site", manifest=SimpleNamespace(value={}), catalog=None)
    pipeline.overrides = None
    pipeline.core = SimpleNamespace()
    pipeline.logger = Logger()
    pipeline.invoker = Invoker()
    pipeline.processors = ()
    pipeline.force_image_format = None
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
    assert validations == [transformed, transformed]


def test_format_precedence_keeps_plugin_default_and_force_overrides_it() -> None:
    pipeline = ArtifactPipeline.__new__(ArtifactPipeline)
    pipeline.config = AppConfig.model_validate({"output": {"image_format": "PNG"}})
    plugin_image = ImageResource("image:1", save_options=ImageSaveOptions(format="JPEG", extension=".jpeg"))
    default_image = ImageResource("image:2")

    pipeline.force_image_format = None
    assert pipeline._effective_save_options(plugin_image).format == "JPEG"
    assert pipeline._effective_save_options(default_image).format == "PNG"

    pipeline.force_image_format = "JPEG"
    forced_jpeg = pipeline._effective_save_options(plugin_image)
    assert forced_jpeg.format == "JPEG"
    assert forced_jpeg.extension is None

    pipeline.force_image_format = "ORIGINAL"
    assert pipeline._effective_save_options(plugin_image) == ImageSaveOptions(format="ORIGINAL")


def test_original_rejects_encoder_options_and_uses_safe_extension_fallbacks() -> None:
    with pytest.raises(ConfigurationError, match="ORIGINAL image format"):
        ArtifactPipeline._validate_original_options(ImageSaveOptions(format="ORIGINAL", extension=".png"))

    assert ArtifactPipeline._original_extension(ImageArtifact(b"", "image/x-icon", "image:1")) == ".ico"
    assert (
        ArtifactPipeline._original_extension(ImageArtifact(b"", "application/octet-stream", "image:1.avif")) == ".avif"
    )
    assert ArtifactPipeline._original_extension(ImageArtifact(b"", "application/octet-stream", "image:1")) == ".bin"


def test_forced_jpeg_replaces_a_plugin_format_and_extension() -> None:
    class Invoker:
        async def transform_image(self, *_args: object, **_kwargs: object) -> ImageArtifact:
            return _args[1]

    class Registry:
        @staticmethod
        def effective_config(*_args: object) -> dict[str, object]:
            return {}

    class Logger:
        async def core(self, *_args: object, **_kwargs: object) -> None:
            return None

    image = ImageResource(
        "https://example.test/image.png", save_options=ImageSaveOptions(format="PNG", extension=".png")
    )
    artifact = ImageArtifact(_image(), "image/png", image.url)
    manifest = DownloadManifest("gallery", (Chapter(1, "chapter", images=(image,)),))
    with ImageProcessor() as core:
        pipeline = ArtifactPipeline.__new__(ArtifactPipeline)
        pipeline.config = AppConfig()
        pipeline.registry = Registry()
        pipeline.site_record = SimpleNamespace(id="com.example.site", manifest=SimpleNamespace(value={}), catalog=None)
        pipeline.overrides = None
        pipeline.core = core
        pipeline.logger = Logger()
        pipeline.invoker = Invoker()
        pipeline.processors = ()
        pipeline.force_image_format = "JPEG"
        result = asyncio.run(pipeline.process(object(), artifact, image, manifest, manifest.chapters[0]))

    assert result.extension == ".jpeg"
    with Image.open(io.BytesIO(result.data)) as decoded:
        assert decoded.format == "JPEG"
