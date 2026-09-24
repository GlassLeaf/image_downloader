"""Public exception hierarchy for image-downloader.

The former catch-all exception is intentionally absent. Callers should catch
the smallest exception family that matches the recovery they can perform.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final


class ImageDownloaderError(Exception):
    """Base class for expected application failures."""

    code = "image_downloader_error"
    reason = "image downloader operation failed"
    _image_failure_reported = False
    _image_failure_context: dict[str, object] | None = None


class ConfigurationError(ImageDownloaderError, ValueError):
    code = "configuration_error"
    reason = "configuration is invalid"


class PluginError(ImageDownloaderError):
    code = "plugin_error"
    reason = "plugin operation failed"


class UnsupportedSiteFeature(PluginError):
    """A site requires a capability outside the v3 plugin contract."""

    code = "unsupported_site_feature"
    reason = "site requires an unsupported feature"


class AuthenticationError(ImageDownloaderError):
    code = "authentication_error"
    reason = "authentication failed"


class SecretNotFound(AuthenticationError):
    code = "secret_not_found"
    reason = "required secret was not found"


class RequestError(ImageDownloaderError):
    """The image or plugin HTTP request could not complete as requested."""

    code = "request_error"
    reason = "HTTP request failed"


class HttpTransportError(RequestError):
    code = "http_transport_error"
    reason = "HTTP transport failed"


class HttpStatusError(RequestError):
    code = "http_status_error"
    reason = "HTTP server returned an error response"

    def __init__(self, status: int, *, response_url: str | None = None) -> None:
        super().__init__(f"HTTP request failed: {status}")
        self.status = status
        self.response_url = response_url


class RedirectPolicyError(RequestError):
    code = "redirect_policy_error"
    reason = "HTTP redirect violates the configured policy"

    def __init__(
        self,
        message: str | None = None,
        *,
        request_url: str | None = None,
        redirect_url: str | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message or self.reason)
        self.request_url = request_url
        self.redirect_url = redirect_url
        self.http_status = http_status
        # ``response_url`` is the URL of the response that carried the rejected
        # redirect, which is the most useful final-response field for diagnostics.
        self.response_url = request_url


class ResponseSizeLimitError(RequestError):
    code = "response_size_limit_error"
    reason = "HTTP response exceeds the configured byte limit"

    def __init__(
        self,
        message: str | None = None,
        *,
        response_url: str | None = None,
        http_status: int | None = None,
        limit_bytes: int | None = None,
    ) -> None:
        super().__init__(message or self.reason)
        self.response_url = response_url
        self.http_status = http_status
        self.limit_bytes = limit_bytes


class ImageProcessingError(ImageDownloaderError):
    """An HTTP response was obtained but cannot be processed as an image."""

    code = "image_processing_error"
    reason = "image processing failed"


class ImageDecodeError(ImageProcessingError):
    code = "image_decode_error"
    reason = "image data cannot be decoded"


class UnsupportedImageFormatError(ImageProcessingError):
    code = "unsupported_image_format"
    reason = "image format is unsupported"

    def __init__(self, image_format: str) -> None:
        super().__init__(f"unsupported image format: {image_format}")
        self.image_format = image_format


class ImageContentTypeError(ImageProcessingError):
    code = "image_content_type_error"
    reason = "image response has a non-image content type"


class ImageMimeMismatchError(ImageProcessingError):
    code = "image_mime_mismatch"
    reason = "declared image MIME does not match image data"


class ImageDimensionLimitError(ImageProcessingError):
    code = "image_dimension_limit_error"
    reason = "image dimensions exceed the configured pixel limit"


class ImageWorkerError(ImageProcessingError):
    code = "image_worker_error"
    reason = "image worker process failed"


class ImageProcessorClosedError(ImageProcessingError):
    code = "image_processor_closed"
    reason = "image processor is closed"


class StorageError(ImageDownloaderError):
    code = "storage_error"
    reason = "storage operation failed"


class OutputAllocationError(StorageError):
    code = "output_allocation_error"
    reason = "could not allocate a unique output filename"


class ExistingFileConflictError(StorageError):
    """An existing output is protected by the ``existing-file=error`` policy."""

    code = "existing_file_conflict"
    reason = "output file already exists and existing-file=error prevents overwrite"

    def __init__(self, relative_path: str | Path) -> None:
        super().__init__(self.reason)
        self.relative_path = Path(relative_path)
        self.policy = "error"


class UpdateStateError(StorageError):
    code = "update_state_error"
    reason = "update state is invalid or cannot be read"


class StorageSafetyError(StorageError):
    """A filesystem operation would leave its configured trusted root."""

    code = "storage_safety_error"
    reason = "storage operation would escape its trusted root"


class InterProcessLockError(StorageError):
    """An inter-process lock could not be acquired or released safely."""

    code = "interprocess_lock_error"
    reason = "inter-process lock operation failed"


@dataclass(frozen=True, slots=True)
class ErrorCatalogEntry:
    """One stable, user-facing contract entry for a public exception."""

    exception: type[ImageDownloaderError]
    attributes: tuple[str, ...] = ()

    @property
    def exception_name(self) -> str:
        return self.exception.__name__

    @property
    def code(self) -> str:
        return self.exception.code

    @property
    def reason(self) -> str:
        return self.exception.reason


ERROR_CATALOG: Final[tuple[ErrorCatalogEntry, ...]] = (
    ErrorCatalogEntry(ImageDownloaderError),
    ErrorCatalogEntry(ConfigurationError),
    ErrorCatalogEntry(PluginError),
    ErrorCatalogEntry(UnsupportedSiteFeature),
    ErrorCatalogEntry(AuthenticationError),
    ErrorCatalogEntry(SecretNotFound),
    ErrorCatalogEntry(RequestError),
    ErrorCatalogEntry(HttpTransportError),
    ErrorCatalogEntry(HttpStatusError, ("status", "response_url")),
    ErrorCatalogEntry(RedirectPolicyError, ("request_url", "redirect_url", "response_url", "http_status")),
    ErrorCatalogEntry(ResponseSizeLimitError, ("response_url", "http_status", "limit_bytes")),
    ErrorCatalogEntry(ImageProcessingError),
    ErrorCatalogEntry(ImageDecodeError),
    ErrorCatalogEntry(UnsupportedImageFormatError, ("image_format",)),
    ErrorCatalogEntry(ImageContentTypeError),
    ErrorCatalogEntry(ImageMimeMismatchError),
    ErrorCatalogEntry(ImageDimensionLimitError),
    ErrorCatalogEntry(ImageWorkerError),
    ErrorCatalogEntry(ImageProcessorClosedError),
    ErrorCatalogEntry(StorageError),
    ErrorCatalogEntry(OutputAllocationError),
    ErrorCatalogEntry(ExistingFileConflictError, ("relative_path", "policy")),
    ErrorCatalogEntry(UpdateStateError),
    ErrorCatalogEntry(StorageSafetyError),
    ErrorCatalogEntry(InterProcessLockError),
)

PUBLIC_EXCEPTION_NAMES: Final[frozenset[str]] = frozenset(entry.exception_name for entry in ERROR_CATALOG)
_ERROR_REASONS = {entry.code: entry.reason for entry in ERROR_CATALOG}
_ERROR_REASONS.update(
    {
        "unexpected_image_failure": "unexpected image failure",
        "unexpected_runtime_error": "unexpected runtime failure",
    }
)


@dataclass(frozen=True, slots=True)
class ErrorInfo:
    """Safe diagnostic information shared by events and machine output."""

    code: str
    reason: str
    exception: str
    message: str
    response_url: str | None = None
    http_status: int | None = None
    output_path: str | None = None


def error_info_for(error: BaseException) -> ErrorInfo:
    """Return the stable public view of an error without exposing ``str(error)``."""

    if not isinstance(error, ImageDownloaderError):
        return ErrorInfo(
            "unexpected_runtime_error",
            _ERROR_REASONS["unexpected_runtime_error"],
            "UnknownError",
            "an unexpected runtime failure occurred",
        )
    code = error.code if error.code in _ERROR_REASONS else ImageDownloaderError.code
    reason = _ERROR_REASONS[code]
    exception = type(error).__name__ if type(error).__name__ in PUBLIC_EXCEPTION_NAMES else "ImageDownloaderError"
    output_path = str(error.relative_path) if isinstance(error, ExistingFileConflictError) else None
    response_url = getattr(error, "response_url", None)
    http_status = getattr(error, "http_status", getattr(error, "status", None))
    return ErrorInfo(code, reason, exception, reason, response_url, http_status, output_path)


def error_catalog_markdown() -> str:
    """Render the checked public exception table embedded in the API document."""

    rows = ["| exception | code | reason | public attributes |", "| --- | --- | --- | --- |"]
    for entry in ERROR_CATALOG:
        attributes = ", ".join(f"`{value}`" for value in entry.attributes) or "—"
        rows.append(f"| `{entry.exception_name}` | `{entry.code}` | {entry.reason} | {attributes} |")
    return "\n".join(rows)


def error_reason_for_code(code: str | None) -> str | None:
    """Return a notification-safe reason for one application error code."""

    if code is None:
        return None
    return _ERROR_REASONS.get(code)
