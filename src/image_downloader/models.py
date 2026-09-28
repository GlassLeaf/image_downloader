"""Immutable public value objects for the local plugin API v3."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import TypeVar

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


def _freeze_header_values(
    value: Mapping[str, tuple[str, ...]], *, field_name: str
) -> Mapping[str, tuple[str, ...]]:
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


@dataclass(frozen=True, slots=True)
class TransportCookie:
    """One name/value pair actually sent in an HTTP Cookie header."""

    name: str
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not isinstance(self.value, str):
            raise TypeError("TransportCookie fields must be strings")


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
