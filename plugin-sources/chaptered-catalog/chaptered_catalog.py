"""v3 sample: build a stable multi-chapter manifest from a catalog API."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from urllib.parse import urlparse

from image_downloader import (
    Chapter,
    DownloadManifest,
    ImageArtifact,
    ImageResource,
    PluginError,
    RequestResponse,
    RequestSpec,
    TransformContext,
    UpdateCandidate,
    UpdateSnapshot,
)


def _origin(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise PluginError("example URL must be an HTTP URL")
    return f"{parsed.scheme}://{parsed.netloc}"


def _path_id(url: str) -> str:
    value = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    if not value:
        raise PluginError("example URL is missing its resource ID")
    return value


def _json(response: RequestResponse, message: str) -> Mapping[str, object]:
    try:
        value = json.loads(response.body)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PluginError(message) from exc
    if not isinstance(value, Mapping):
        raise PluginError(message)
    return value


def _text(value: object, message: str) -> str:
    if not isinstance(value, str) or not value:
        raise PluginError(message)
    return value


class ChapteredCatalogPlugin:
    def validate_config(self, config: Mapping[str, object], app_settings: Mapping[str, object]) -> None:
        del app_settings
        if config:
            raise ValueError("chaptered catalog does not accept configuration")

    def matches(self, url: str) -> bool:
        parsed = urlparse(url)
        return parsed.scheme in {"http", "https"} and parsed.hostname == "catalog.example.test"

    async def inspect(self, url: str, context) -> DownloadManifest:
        response = await context.requests.execute(
            RequestSpec(f"{_origin(url)}/api/works/{_path_id(url)}", headers={"Accept": "application/json"})
        )
        work = _json(response, "catalog work response is invalid").get("work")
        if not isinstance(work, Mapping) or not isinstance(work.get("chapters"), list):
            raise ValueError("catalog work response is invalid")
        title = _text(work.get("title"), "catalog work title is missing")
        chapters: list[Chapter] = []
        for number, raw in enumerate(work["chapters"], 1):
            if not isinstance(raw, Mapping) or not isinstance(raw.get("images"), list):
                raise ValueError("catalog chapter is invalid")
            images = tuple(
                ImageResource(
                    _text(item.get("url"), "catalog image URL is missing"),
                    index=index,
                    referer=url,
                    image_id=_text(item.get("id"), "catalog image ID is missing"),
                )
                for index, item in enumerate(raw["images"], 1)
                if isinstance(item, Mapping)
            )
            if len(images) != len(raw["images"]):
                raise ValueError("catalog image is invalid")
            chapters.append(
                Chapter(
                    number,
                    str(raw.get("title") or title),
                    images=images,
                    chapter_id=_text(raw.get("id"), "catalog chapter ID is missing"),
                )
            )
        return DownloadManifest(
            title,
            tuple(chapters),
            content_id=_text(work.get("id"), "catalog work ID is missing"),
            revision=str(work.get("revision") or "") or None,
            metadata={"pattern": "chaptered-catalog"},
        )

    async def create_image_request(self, image: ImageResource, context) -> RequestSpec:
        del context
        return RequestSpec(image.url, referer=image.referer)

    async def recover_image_request(self, image, failed, response, context) -> RequestSpec | None:
        del image, failed, response, context
        return None

    def auth_flow(self, context):
        del context
        return None

    async def transform_image(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact:
        del context
        return artifact

    async def check_updates(self, url: str, context) -> UpdateSnapshot:
        response = await context.requests.execute(
            RequestSpec(f"{_origin(url)}/api/updates", headers={"Accept": "application/json"})
        )
        works = _json(response, "catalog update response is invalid").get("works")
        if not isinstance(works, list):
            raise ValueError("catalog updates are invalid")
        candidates = tuple(
            UpdateCandidate(
                _text(item.get("url"), "catalog update URL is missing"),
                content_id=_text(item.get("id"), "catalog update ID is missing"),
                revision=str(item.get("revision") or "") or None,
            )
            for item in works
            if isinstance(item, Mapping)
        )
        if len(candidates) != len(works):
            raise ValueError("catalog update is invalid")
        return UpdateSnapshot(url, candidates, datetime.now(UTC))
