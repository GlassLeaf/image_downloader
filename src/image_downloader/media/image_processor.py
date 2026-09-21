from __future__ import annotations

import io
import threading
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import PurePosixPath
from types import TracebackType
from typing import TypeVar
from urllib.parse import urlparse

from ..exceptions import (
    ConfigurationError,
    ImageDecodeError,
    ImageDimensionLimitError,
    ImageProcessorClosedError,
    ImageWorkerError,
    UnsupportedImageFormatError,
)
from ..models import ImageSaveOptions

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

_ResultT = TypeVar("_ResultT")


class ImageProcessor:
    """Run all Pillow work in one service-owned, long-lived worker process."""

    def __init__(self) -> None:
        self._executor = ProcessPoolExecutor(max_workers=1, initializer=_initialize_worker)
        self._lifecycle_lock = threading.Lock()
        self._closed = False

    def process(
        self,
        data: bytes,
        *,
        source_url: str,
        content_type: str,
        options: ImageSaveOptions | None = None,
        max_pixels: int | None = None,
    ) -> tuple[bytes, str]:
        return self._submit(
            _process_image,
            data,
            source_url,
            content_type,
            options or ImageSaveOptions(),
            max_pixels,
        )

    def inspect(self, data: bytes, *, max_pixels: int | None = None) -> str:
        """Validate image bytes and return their detected MIME type without encoding."""
        return self._submit(_inspect_image, data, max_pixels)

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
    from PIL import Image

    # The application owns dimension policy inside its isolated worker. The
    # parent process and other Pillow consumers retain their own global value.
    Image.MAX_IMAGE_PIXELS = None


def _process_image(
    data: bytes,
    source_url: str,
    content_type: str,
    options: ImageSaveOptions,
    max_pixels: int | None,
) -> tuple[bytes, str]:
    _validate(options)
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            _check_dimensions(image.size, max_pixels)
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            _check_dimensions(image.size, max_pixels)
            detected = (image.format or "").upper()
            requested = options.format.upper() if options.format else None
            source_format = _format_from_url(source_url) or _format_from_content_type(content_type)
            # Preserve a matching URL/Content-Type format; correct misleading metadata using the decoded format.
            target = requested or (source_format if source_format == detected else detected) or detected
            if target not in _FORMAT_TO_EXTENSION:
                raise UnsupportedImageFormatError(target)
            extension = _extension_for(options, target)
            if not requested and target == detected and extension == _FORMAT_TO_EXTENSION[target]:
                return data, extension
            if target == "JPEG":
                image = image.convert("RGB")
            output = io.BytesIO()
            image.save(output, format=target, **_pillow_options(options, target))
            return output.getvalue(), extension
    except (ConfigurationError, ImageDecodeError, ImageDimensionLimitError, UnsupportedImageFormatError):
        raise
    except Exception as exc:
        raise ImageDecodeError("invalid image data") from exc


def _inspect_image(data: bytes, max_pixels: int | None) -> str:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            _check_dimensions(image.size, max_pixels)
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            _check_dimensions(image.size, max_pixels)
            return Image.MIME.get(image.format or "", "application/octet-stream").lower()
    except ImageDimensionLimitError:
        raise
    except Exception as exc:
        raise ImageDecodeError("invalid image data") from exc


def _format_from_url(url: str) -> str | None:
    return _EXTENSION_TO_FORMAT.get(PurePosixPath(urlparse(url).path).suffix.lower())


def _format_from_content_type(content_type: str) -> str | None:
    return _CONTENT_TYPE_TO_FORMAT.get(content_type.split(";", 1)[0].strip().lower())


def _extension_for(options: ImageSaveOptions, image_format: str) -> str:
    if options.extension:
        ext = options.extension.lower()
        if not ext.startswith(".") or ext not in _EXTENSION_TO_FORMAT:
            raise ConfigurationError(f"unsupported image extension: {options.extension}")
        if _EXTENSION_TO_FORMAT[ext] != image_format:
            raise ConfigurationError("extension must match the selected image format")
        return ext
    return _FORMAT_TO_EXTENSION[image_format]


def _validate(options: ImageSaveOptions) -> None:
    if options.format and options.format.upper() not in _FORMAT_TO_EXTENSION:
        raise ConfigurationError(f"unsupported image format: {options.format}")
    if options.quality is not None and not 0 <= options.quality <= 100:
        raise ConfigurationError("quality must be between 0 and 100")
    if options.compress_level is not None and not 0 <= options.compress_level <= 9:
        raise ConfigurationError("compress_level must be between 0 and 9")


def _check_dimensions(size: tuple[int, int], max_pixels: int | None) -> None:
    if max_pixels is not None and size[0] * size[1] > max_pixels:
        raise ImageDimensionLimitError("image dimensions exceed the configured pixel limit")


def _pillow_options(options: ImageSaveOptions, image_format: str) -> dict[str, object]:
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
