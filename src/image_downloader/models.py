"""Immutable public value objects for the local plugin API v3."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Literal, TypeVar

from .exceptions import ErrorInfo
from .immutable import freeze_json as freeze_json
from .immutable import thaw_json as thaw_json

T = TypeVar("T")


def freeze_mapping(value: Mapping[str, T] | None = None) -> Mapping[str, T]:
    return MappingProxyType(dict(value or {}))


def _freeze_string_mapping(value: Mapping[str, str], *, field_name: str) -> Mapping[str, str]:
    if not isinstance(value, Mapping) or any(
        not isinstance(key, str) or not isinstance(item, str) for key, item in value.items()
    ):
        raise TypeError(f"{field_name} must map strings to strings")
    return freeze_mapping(value)


def _freeze_header_values(value: Mapping[str, tuple[str, ...]], *, field_name: str) -> Mapping[str, tuple[str, ...]]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    frozen: dict[str, tuple[str, ...]] = {}
    for key, items in value.items():
        if not isinstance(key, str) or not isinstance(items, tuple) or any(not isinstance(item, str) for item in items):
            raise TypeError(f"{field_name} must map strings to tuples of strings")
        frozen[key] = tuple(items)
    return freeze_mapping(frozen)


@dataclass(frozen=True, slots=True)
class RequestSpec:
    url: str
    method: str = "GET"
    headers: Mapping[str, str] = field(default_factory=freeze_mapping)
    cookies: Mapping[str, str] = field(default_factory=freeze_mapping)
    referer: str | None = None
    query: Mapping[str, str] = field(default_factory=freeze_mapping)
    form: Mapping[str, str] = field(default_factory=freeze_mapping)
    json: object | None = None
    auth_required: bool = True
    retry_non_idempotent: bool = False

    def __post_init__(self) -> None:
        if self.form and self.json is not None:
            raise ValueError("RequestSpec.form and RequestSpec.json are mutually exclusive")
        object.__setattr__(self, "headers", freeze_mapping(self.headers))
        object.__setattr__(self, "cookies", freeze_mapping(self.cookies))
        object.__setattr__(self, "query", freeze_mapping(self.query))
        object.__setattr__(self, "form", freeze_mapping(self.form))
        object.__setattr__(self, "json", freeze_json(self.json))


@dataclass(frozen=True, slots=True)
class RequestResponse:
    url: str
    status: int
    headers: Mapping[str, str]
    body: bytes

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", freeze_mapping(self.headers))


@dataclass(frozen=True, slots=True)
class ImageFetchRequest:
    """A resolved image request plus plugin-defined transform data.

    ``RequestSpec`` remains valid as a return value from the image request and
    recovery hooks.  Plugins opt in to this DTO only when request-time data is
    needed by image transforms.
    """

    request: RequestSpec
    plugin_data: Mapping[str, str] = field(default_factory=freeze_mapping)

    def __post_init__(self) -> None:
        if not isinstance(self.request, RequestSpec):
            raise TypeError("ImageFetchRequest.request must be RequestSpec")
        object.__setattr__(self, "plugin_data", _freeze_string_mapping(self.plugin_data, field_name="plugin_data"))


class ImageRequestResolutionStatus(StrEnum):
    """Outcome of inspecting one manifest image without fetching its body."""

    RESOLVED = "resolved"
    PARTIALLY_RESOLVED = "partially_resolved"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ImageRequestResolutionFailure:
    """Stable diagnostic data for one inspection resolution failure."""

    code: str
    reason: str
    exception: str
    phase: str = "create_image_request"

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) for value in (self.code, self.reason, self.exception, self.phase)):
            raise TypeError("ImageRequestResolutionFailure fields must be strings")
        if self.phase not in {"create_image_request", "effective_request"}:
            raise ValueError("ImageRequestResolutionFailure.phase is invalid")


@dataclass(frozen=True, slots=True)
class ImageRequestResolution:
    """One request returned by ``create_image_request`` during inspection.

    Positions are one-based positions in the manifest arrays and deliberately
    differ from plugin-controlled ``Chapter.number`` and ``ImageResource.index``.
    """

    chapter_position: int
    image_position: int
    status: ImageRequestResolutionStatus
    request: RequestSpec | None = None
    plugin_data: Mapping[str, str] = field(default_factory=freeze_mapping)
    uses_image_fetch_request: bool | None = None
    failure: ImageRequestResolutionFailure | None = None
    effective_request: EffectiveRequestPreview | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.chapter_position, bool)
            or not isinstance(self.chapter_position, int)
            or self.chapter_position < 1
            or isinstance(self.image_position, bool)
            or not isinstance(self.image_position, int)
            or self.image_position < 1
        ):
            raise ValueError("ImageRequestResolution positions must be positive integers")
        object.__setattr__(
            self,
            "plugin_data",
            _freeze_string_mapping(self.plugin_data, field_name="ImageRequestResolution.plugin_data"),
        )
        if self.status is ImageRequestResolutionStatus.RESOLVED:
            if not isinstance(self.request, RequestSpec) or self.failure is not None:
                raise TypeError("resolved ImageRequestResolution requires request and no failure")
            if not isinstance(self.uses_image_fetch_request, bool):
                raise TypeError("resolved ImageRequestResolution requires uses_image_fetch_request")
            if self.effective_request is not None and not isinstance(self.effective_request, EffectiveRequestPreview):
                raise TypeError(
                    "resolved ImageRequestResolution.effective_request must be EffectiveRequestPreview or None"
                )
        elif self.status is ImageRequestResolutionStatus.PARTIALLY_RESOLVED:
            if not isinstance(self.request, RequestSpec) or not isinstance(self.uses_image_fetch_request, bool):
                raise TypeError(
                    "partially resolved ImageRequestResolution requires request and uses_image_fetch_request"
                )
            if not isinstance(self.failure, ImageRequestResolutionFailure) or self.effective_request is not None:
                raise TypeError("partially resolved ImageRequestResolution requires failure and no effective_request")
        elif self.status is ImageRequestResolutionStatus.FAILED:
            if (
                self.request is not None
                or self.failure is None
                or self.uses_image_fetch_request is not None
                or self.effective_request is not None
            ):
                raise TypeError("failed ImageRequestResolution requires failure only")
            if not isinstance(self.failure, ImageRequestResolutionFailure):
                raise TypeError("failed ImageRequestResolution requires ImageRequestResolutionFailure")
        else:
            raise TypeError("ImageRequestResolution.status is invalid")


@dataclass(frozen=True, slots=True)
class TransportCookie:
    """One name/value pair actually sent in an HTTP Cookie header."""

    name: str
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not isinstance(self.value, str):
            raise TypeError("TransportCookie fields must be strings")


@dataclass(frozen=True, slots=True)
class TransportHeader:
    """One header exactly as prepared for one HTTP request."""

    name: str
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not isinstance(self.value, str):
            raise TypeError("TransportHeader fields must be strings")


@dataclass(frozen=True, slots=True)
class EffectiveRequestPreview:
    """A no-send snapshot of the HTTP request immediately before transport."""

    method: str
    url: str
    headers: tuple[TransportHeader, ...]
    cookies: tuple[TransportCookie, ...] = ()
    body: bytes = b""

    def __post_init__(self) -> None:
        if not isinstance(self.method, str) or not isinstance(self.url, str):
            raise TypeError("EffectiveRequestPreview.method and url must be strings")
        if any(not isinstance(header, TransportHeader) for header in self.headers):
            raise TypeError("EffectiveRequestPreview.headers must contain TransportHeader values")
        if any(not isinstance(cookie, TransportCookie) for cookie in self.cookies):
            raise TypeError("EffectiveRequestPreview.cookies must contain TransportCookie values")
        if not isinstance(self.body, bytes):
            raise TypeError("EffectiveRequestPreview.body must be bytes")
        object.__setattr__(self, "headers", tuple(self.headers))
        object.__setattr__(self, "cookies", tuple(self.cookies))


@dataclass(frozen=True, slots=True)
class TransportRequestMetadata:
    """A snapshot of one actual HTTP request hop for an image fetch."""

    url: str
    headers: Mapping[str, tuple[str, ...]]
    cookies: tuple[TransportCookie, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.url, str):
            raise TypeError("TransportRequestMetadata.url must be a string")
        object.__setattr__(self, "headers", _freeze_header_values(self.headers, field_name="headers"))
        if any(not isinstance(cookie, TransportCookie) for cookie in self.cookies):
            raise TypeError("TransportRequestMetadata.cookies must contain TransportCookie values")
        object.__setattr__(self, "cookies", tuple(self.cookies))


@dataclass(frozen=True, slots=True)
class ImageTransportMetadata:
    """Successful image-fetch transport data exposed only to transforms."""

    initial_request: TransportRequestMetadata
    final_request: TransportRequestMetadata
    response_url: str
    response_headers: Mapping[str, tuple[str, ...]]
    plugin_data: Mapping[str, str] = field(default_factory=freeze_mapping)
    is_redacted: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.initial_request, TransportRequestMetadata) or not isinstance(
            self.final_request, TransportRequestMetadata
        ):
            raise TypeError("ImageTransportMetadata request fields must be TransportRequestMetadata")
        if not isinstance(self.response_url, str):
            raise TypeError("ImageTransportMetadata.response_url must be a string")
        object.__setattr__(
            self,
            "response_headers",
            _freeze_header_values(self.response_headers, field_name="response_headers"),
        )
        object.__setattr__(self, "plugin_data", _freeze_string_mapping(self.plugin_data, field_name="plugin_data"))
        if not isinstance(self.is_redacted, bool):
            raise TypeError("ImageTransportMetadata.is_redacted must be bool")


@dataclass(frozen=True, slots=True)
class ImageSaveOptions:
    format: str | None = None
    extension: str | None = None
    quality: int | None = None
    optimize: bool | None = None
    progressive: bool | None = None
    lossless: bool | None = None
    compress_level: int | None = None
    exif: bool = False


@dataclass(frozen=True, slots=True)
class ImageResource:
    """A manifest image identified by an opaque, non-empty string locator.

    ``url`` is retained as the public field name for compatibility, but it is
    not necessarily a network URL.  A plugin resolves it to the actual
    absolute HTTP(S) ``RequestSpec.url`` in ``create_image_request``.
    """

    url: str
    index: int = 1
    referer: str | None = None
    headers: Mapping[str, str] = field(default_factory=freeze_mapping)
    save_options: ImageSaveOptions = field(default_factory=ImageSaveOptions)
    image_id: str | None = None
    metadata: Mapping[str, str] = field(default_factory=freeze_mapping)
    original_filename: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", freeze_mapping(self.headers))
        object.__setattr__(self, "metadata", _freeze_string_mapping(self.metadata, field_name="ImageResource.metadata"))


@dataclass(frozen=True, slots=True)
class Chapter:
    number: int
    title: str
    subtitle: str = ""
    images: tuple[ImageResource, ...] = ()
    chapter_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "images", tuple(self.images))


@dataclass(frozen=True, slots=True)
class DownloadManifest:
    title: str
    chapters: tuple[Chapter, ...]
    content_id: str | None = None
    author: str | None = None
    access: str | None = None
    revision: str | None = None
    metadata: Mapping[str, str] = field(default_factory=freeze_mapping)

    def __post_init__(self) -> None:
        object.__setattr__(self, "chapters", tuple(self.chapters))
        object.__setattr__(
            self,
            "metadata",
            _freeze_string_mapping(self.metadata, field_name="DownloadManifest.metadata"),
        )


@dataclass(frozen=True, slots=True)
class ManifestInspectionResult:
    """A manifest and optional per-image request resolutions without image fetches."""

    source_url: str
    plugin_id: str
    manifest: DownloadManifest
    request_resolution_performed: bool
    image_requests: tuple[ImageRequestResolution, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.source_url, str) or not isinstance(self.plugin_id, str):
            raise TypeError("ManifestInspectionResult source_url and plugin_id must be strings")
        if not isinstance(self.manifest, DownloadManifest):
            raise TypeError("ManifestInspectionResult.manifest must be DownloadManifest")
        if not isinstance(self.request_resolution_performed, bool):
            raise TypeError("ManifestInspectionResult.request_resolution_performed must be bool")
        requests = tuple(self.image_requests)
        if any(not isinstance(value, ImageRequestResolution) for value in requests):
            raise TypeError("ManifestInspectionResult.image_requests must contain ImageRequestResolution values")
        if not self.request_resolution_performed and requests:
            raise ValueError("manifest-only inspection cannot contain image request resolutions")
        object.__setattr__(self, "image_requests", requests)

    @property
    def failures(self) -> tuple[ImageRequestResolution, ...]:
        return tuple(
            value for value in self.image_requests if value.status is not ImageRequestResolutionStatus.RESOLVED
        )


@dataclass(frozen=True, slots=True)
class ImageArtifact:
    """Image bytes with the manifest locator that identified their source.

    ``source_url`` preserves the source ``ImageResource.url`` locator rather
    than the final response URL, so transforms can keep using the stable
    per-image value supplied by the manifest.
    """

    data: bytes
    content_type: str
    source_url: str
    image_id: str | None = None
    extension: str | None = None
    history: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "history", tuple(self.history))


class FailureKind(StrEnum):
    FETCH = "fetch"
    PROCESS = "process"
    SAVE = "save"


@dataclass(frozen=True, slots=True)
class ImageFailure:
    kind: FailureKind
    exception_type: str
    message: str
    code: str = "unexpected_image_failure"
    reason: str = "unexpected image failure"
    response_url: str | None = None
    http_status: int | None = None
    output_path: str | None = None
    transport: str | None = None


class ImageOutcomeKind(StrEnum):
    SAVED = "saved"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ImageOutcome:
    image: ImageResource
    kind: ImageOutcomeKind
    path: str | None = None
    failure: ImageFailure | None = None


@dataclass(frozen=True, slots=True)
class ChapterResult:
    chapter: Chapter
    outcomes: tuple[ImageOutcome, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcomes", tuple(self.outcomes))


@dataclass(frozen=True, slots=True)
class DownloadResult:
    source_url: str
    manifest: DownloadManifest
    chapters: tuple[ChapterResult, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "chapters", tuple(self.chapters))

    @property
    def saved_files(self) -> tuple[str, ...]:
        return tuple(
            item.path
            for chapter in self.chapters
            for item in chapter.outcomes
            if item.kind is ImageOutcomeKind.SAVED and item.path is not None
        )

    @property
    def skipped_files(self) -> tuple[str, ...]:
        return tuple(
            item.path
            for chapter in self.chapters
            for item in chapter.outcomes
            if item.kind is ImageOutcomeKind.SKIPPED and item.path is not None
        )

    @property
    def failures(self) -> tuple[ImageFailure, ...]:
        return tuple(item.failure for chapter in self.chapters for item in chapter.outcomes if item.failure is not None)


@dataclass(frozen=True, slots=True)
class UpdateCandidate:
    url: str
    content_id: str | None = None
    revision: str | None = None


class UpdateChangeKind(StrEnum):
    ADDED = "added"
    CHANGED = "changed"
    REMOVED = "removed"


@dataclass(frozen=True, slots=True)
class UpdateChange:
    kind: UpdateChangeKind
    url: str
    content_id: str | None = None
    revision: str | None = None


@dataclass(frozen=True, slots=True)
class UpdateSnapshot:
    source_url: str
    candidates: tuple[UpdateCandidate, ...]
    checked_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidates", tuple(self.candidates))


@dataclass(frozen=True, slots=True)
class UpdateResult:
    source_url: str
    plugin_id: str
    changes: tuple[UpdateChange, ...]
    checked_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "changes", tuple(self.changes))


@dataclass(frozen=True, slots=True)
class WorkflowItemResult:
    url: str
    reasons: tuple[str, ...]
    status: Literal["success", "partial", "failed", "unprocessed"]
    download: DownloadResult | None = None
    error: ErrorInfo | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "reasons", tuple(self.reasons))


@dataclass(frozen=True, slots=True)
class WorkflowResult:
    source_url: str
    download_scope: Literal["all", "updated"]
    snapshot: UpdateSnapshot | None
    changes: tuple[UpdateChange, ...]
    items: tuple[WorkflowItemResult, ...]
    stop_error: ErrorInfo | None = None
    cancelled: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "changes", tuple(self.changes))
        object.__setattr__(self, "items", tuple(self.items))

    @property
    def selected_urls(self) -> tuple[str, ...]:
        return tuple(item.url for item in self.items)
