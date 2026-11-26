from __future__ import annotations

import io
import threading
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from pathlib import PurePosixPath
from types import TracebackType
from typing import TYPE_CHECKING, TypeVar
from urllib.parse import urlparse

from ..exceptions import (
    ConfigurationError,
    ImageDecodeError,
    ImageDimensionLimitError,
    ImageProcessorClosedError,
    ImageWorkerError,
    UnsupportedImageFormatError,
)

if TYPE_CHECKING:
    from PIL import Image


_EXTENSION_TO_FORMAT = {
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".png": "PNG",
    ".webp": "WEBP",
    ".gif": "GIF",
    ".bmp": "BMP",
    ".tif": "TIFF",
    ".tiff": "TIFF",
}
_FORMAT_TO_EXTENSION = {"JPEG": ".jpeg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif", "BMP": ".bmp", "TIFF": ".tiff"}
_CONTENT_TYPE_TO_FORMAT = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WEBP",
    "image/gif": "GIF",
    "image/bmp": "BMP",
    "image/tiff": "TIFF",
}


@dataclass(frozen=True, slots=True)
class ImageEncodingOptions:
    """Resolved encoder settings, separate from plugin minimum requirements."""

    format: str | None = None
    extension: str | None = None
    quality: int | None = None
    optimize: bool | None = None
    progressive: bool | None = None
    lossless: bool | None = None
    compress_level: int | None = None
    exif: bool = False


@dataclass(frozen=True, slots=True)
class ImageDescription:
    format: str
    content_type: str
    width: int
    height: int


_ResultT = TypeVar("_ResultT")


class ImageProcessor:
    """Run all Pillow work in one service-owned, long-lived worker process."""

    def __init__(self) -> None:
        self._lifecycle_lock = threading.Lock()
        self._closed = False
        self._executor = ProcessPoolExecutor(max_workers=1, initializer=_initialize_worker)

    def process(
        self,
        data: bytes,
        *,
        source_url: str,
        content_type: str,
        options: ImageEncodingOptions | None = None,
        max_pixels: int | None = None,
    ) -> tuple[bytes, str]:
        return self._submit(
            _process_image,
            data,
            source_url,
            content_type,
            options or ImageEncodingOptions(),
            max_pixels,
        )

    def inspect(self, data: bytes, *, max_pixels: int | None = None) -> str:
        """Validate image bytes and return their detected MIME type without encoding."""
        return self._submit(_inspect_image, data, max_pixels)

    def describe(self, data: bytes, *, max_pixels: int | None = None) -> ImageDescription:
        """Read format and display dimensions without re-encoding."""
        return self._submit(_describe_image, data, max_pixels)

    def close(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=True, cancel_futures=True)

    def __enter__(self) -> ImageProcessor:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _submit(self, operation: Callable[..., _ResultT], *args: object) -> _ResultT:
        with self._lifecycle_lock:
            if self._closed:
                raise ImageProcessorClosedError("image processor is closed")
            try:
                future = self._executor.submit(operation, *args)
            except BrokenProcessPool as exc:
                raise ImageWorkerError("image worker process failed") from exc
        try:
            return future.result()
        except BrokenProcessPool as exc:
            raise ImageWorkerError("image worker process failed") from exc


def _initialize_worker() -> None:
    from PIL import Image, ImageFile

    # The application owns dimension policy inside its isolated worker. The
    # parent process and other Pillow consumers retain their own global value.
    Image.MAX_IMAGE_PIXELS = None
    ImageFile.LOAD_TRUNCATED_IMAGES = False


def _process_image(
    data: bytes,
    source_url: str,
    content_type: str,
    options: ImageEncodingOptions,
    max_pixels: int | None,
) -> tuple[bytes, str]:
    _validate(options)
    try:
        from PIL import Image

        _inspect_image(data, max_pixels)
        with Image.open(io.BytesIO(data)) as image:
            _check_dimensions(image.size, max_pixels)
            detected = (image.format or "").upper()
            requested = options.format.upper() if options.format else None
            source_format = _format_from_locator(source_url) or _format_from_content_type(content_type)
            # A locator suffix and Content-Type are hints; decoded bytes decide the final format.
            target = requested or (source_format if source_format == detected else detected) or detected
            if target not in _FORMAT_TO_EXTENSION:
                raise UnsupportedImageFormatError(target)
            extension = _extension_for(options, target)
            if not requested and target == detected and extension == _FORMAT_TO_EXTENSION[target]:
                return data, extension
            if options.exif and target not in {"JPEG", "PNG", "WEBP", "TIFF"}:
                raise ConfigurationError(f"EXIF preservation is not supported for {target}")
            save_image, encoder_options = _encoding_frame(image, options, target)
            if getattr(image, "n_frames", 1) > 1 and target in {"PNG", "WEBP", "TIFF", "GIF"}:
                frames = [save_image]
                durations = [image.info.get("duration", 0)]
                loop = image.info.get("loop")
                for index in range(1, getattr(image, "n_frames", 1)):
                    image.seek(index)
                    frame, _ = _encoding_frame(image, options, target)
                    frames.append(frame)
                    durations.append(image.info.get("duration", 0))
                if target in {"PNG", "WEBP"}:
                    frames = [frame.convert("RGBA") for frame in frames]
                save_image = frames[0]
                encoder_options.update(save_all=True, append_images=frames[1:])
                if target != "TIFF":
                    encoder_options["duration"] = durations
                    if loop is not None:
                        encoder_options["loop"] = loop
            output = io.BytesIO()
            save_image.save(output, format=target, **encoder_options)
            return output.getvalue(), extension
    except (ConfigurationError, ImageDecodeError, ImageDimensionLimitError, UnsupportedImageFormatError):
        raise
    except Exception as exc:
        raise ImageDecodeError("invalid image data") from exc


def _encoding_frame(
    image: Image.Image, options: ImageEncodingOptions, target: str
) -> tuple[Image.Image, dict[str, object]]:
    from PIL import ImageOps

    image.load()
    encoder_options = _pillow_options(options, target)
    if options.exif:
        exif = image.getexif()
        if exif:
            encoder_options["exif"] = exif.tobytes()
        save_image = image.copy()
    else:
        # Keep the display orientation when removing its EXIF tag.
        oriented = ImageOps.exif_transpose(image)
        assert oriented is not None
        save_image = oriented
        save_image.info.pop("exif", None)
        save_image.info.pop("Raw profile type exif", None)
    if target == "JPEG":
        save_image = save_image.convert("RGB")
    return save_image, encoder_options


def _inspect_image(data: bytes, max_pixels: int | None) -> str:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            _check_dimensions(image.size, max_pixels)
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            detected = Image.MIME.get(image.format or "", "application/octet-stream").lower()
            for frame in range(getattr(image, "n_frames", 1)):
                image.seek(frame)
                _check_dimensions(image.size, max_pixels)
                image.load()
            return detected
    except ImageDimensionLimitError:
        raise
    except Exception as exc:
        raise ImageDecodeError("invalid image data") from exc


def _describe_image(data: bytes, max_pixels: int | None) -> ImageDescription:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            _check_dimensions(image.size, max_pixels)
            width, height = image.size
            if image.getexif().get(274) in {5, 6, 7, 8}:
                width, height = height, width
            return ImageDescription(
                (image.format or "").upper(),
                Image.MIME.get(image.format or "", "application/octet-stream").lower(),
                width,
                height,
            )
    except ImageDimensionLimitError:
        raise
    except Exception as exc:
        raise ImageDecodeError("cannot determine image dimensions") from exc


def _format_from_locator(locator: str) -> str | None:
    """Use a locator suffix as a format hint without requiring an HTTP URL."""
    return _EXTENSION_TO_FORMAT.get(PurePosixPath(urlparse(locator).path).suffix.lower())


def _format_from_content_type(content_type: str) -> str | None:
    return _CONTENT_TYPE_TO_FORMAT.get(content_type.split(";", 1)[0].strip().lower())


def _extension_for(options: ImageEncodingOptions, image_format: str) -> str:
    if options.extension:
        ext = options.extension.lower()
        if not ext.startswith(".") or ext not in _EXTENSION_TO_FORMAT:
            raise ConfigurationError(f"unsupported image extension: {options.extension}")
        if _EXTENSION_TO_FORMAT[ext] != image_format:
            raise ConfigurationError("extension must match the selected image format")
        return ext
    return _FORMAT_TO_EXTENSION[image_format]


def _validate(options: ImageEncodingOptions) -> None:
    if options.format and options.format.upper() not in _FORMAT_TO_EXTENSION:
        raise ConfigurationError(f"unsupported image format: {options.format}")
    if options.quality is not None and not 0 <= options.quality <= 100:
        raise ConfigurationError("quality must be between 0 and 100")
    if options.compress_level is not None and not 0 <= options.compress_level <= 9:
        raise ConfigurationError("compress_level must be between 0 and 9")


def _check_dimensions(size: tuple[int, int], max_pixels: int | None) -> None:
    if max_pixels is not None and size[0] * size[1] > max_pixels:
        raise ImageDimensionLimitError("image dimensions exceed the configured pixel limit")


def _pillow_options(options: ImageEncodingOptions, image_format: str) -> dict[str, object]:
    result: dict[str, object] = {}
    if options.quality is not None and image_format in {"JPEG", "WEBP"}:
        result["quality"] = options.quality
    if options.optimize is not None and image_format in {"JPEG", "PNG"}:
        result["optimize"] = options.optimize
    if options.progressive is not None and image_format == "JPEG":
        result["progressive"] = options.progressive
    if options.lossless is not None and image_format == "WEBP":
        result["lossless"] = options.lossless
    if options.compress_level is not None and image_format == "PNG":
        result["compress_level"] = options.compress_level
    return result
