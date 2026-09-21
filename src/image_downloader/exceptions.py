"""Public exception hierarchy for image-downloader.

The former catch-all exception is intentionally absent. Callers should catch
the smallest exception family that matches the recovery they can perform.
"""

from __future__ import annotations


class ImageDownloaderError(Exception):
    """Base class for expected application failures."""

    code = "image_downloader_error"
    reason = "image downloader operation failed"


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

    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP request failed: {status}")
        self.status = status


class RedirectPolicyError(RequestError):
    code = "redirect_policy_error"
    reason = "HTTP redirect violates the configured policy"


class ResponseSizeLimitError(RequestError):
    code = "response_size_limit_error"
    reason = "HTTP response exceeds the configured byte limit"


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


_ERROR_REASONS = {
    cls.code: cls.reason
    for cls in (
        ImageDownloaderError,
        ConfigurationError,
        PluginError,
        UnsupportedSiteFeature,
        AuthenticationError,
        SecretNotFound,
        RequestError,
        HttpTransportError,
        HttpStatusError,
        RedirectPolicyError,
        ResponseSizeLimitError,
        ImageProcessingError,
        ImageDecodeError,
        UnsupportedImageFormatError,
        ImageContentTypeError,
        ImageMimeMismatchError,
        ImageDimensionLimitError,
        ImageWorkerError,
        ImageProcessorClosedError,
        StorageError,
        OutputAllocationError,
        UpdateStateError,
        StorageSafetyError,
        InterProcessLockError,
    )
}
_ERROR_REASONS["unexpected_image_failure"] = "unexpected image failure"


def error_reason_for_code(code: str | None) -> str | None:
    """Return a notification-safe reason for one application error code."""

    if code is None:
        return None
    return _ERROR_REASONS.get(code)
