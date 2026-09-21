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
    url: str
    index: int = 1
    referer: str | None = None
    headers: Mapping[str, str] = field(default_factory=freeze_mapping)
    save_options: ImageSaveOptions = field(default_factory=ImageSaveOptions)
    image_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", freeze_mapping(self.headers))


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
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in self.metadata.items()):
            raise TypeError("DownloadManifest.metadata must map strings to strings")
        object.__setattr__(self, "metadata", freeze_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class ImageArtifact:
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
