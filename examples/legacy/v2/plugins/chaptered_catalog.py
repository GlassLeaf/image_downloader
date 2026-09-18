"""Chapter catalog API: construct a stable multi-chapter manifest and snapshot."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlparse

from image_downloader import (
    Chapter,
    DownloadManifest,
    ImageArtifact,
    ImageResource,
    PluginDescriptor,
    PluginExecutionContext,
    RequestResponse,
    RequestSpec,
    TransformContext,
    UpdateCandidate,
    UpdateSnapshot,
)

from ._support import origin, path_id, required_text, response_json


class ChapteredCatalogPlugin:
    descriptor = PluginDescriptor("example.chaptered-catalog", priority=20)

    def matches(self, url: str) -> bool:
        parsed = urlparse(url)
        return parsed.scheme in {"http", "https"} and parsed.hostname == "catalog.example.test"

    async def inspect(self, url: str, context: PluginExecutionContext) -> DownloadManifest:
        response = await context.requests.execute(
            RequestSpec(f"{origin(url)}/api/works/{path_id(url)}", headers={"Accept": "application/json"})
        )
        payload = response_json(response, message="catalog work response is invalid")
        work = payload.get("work")
        if not isinstance(work, dict):
            raise ValueError("catalog work response is invalid")
        title = required_text(work.get("title"), message="catalog work title is missing")
        raw_chapters = work.get("chapters")
        if not isinstance(raw_chapters, list):
            raise ValueError("catalog chapters are invalid")
        chapters: list[Chapter] = []
        for number, raw_chapter in enumerate(raw_chapters, start=1):
            if not isinstance(raw_chapter, dict):
                raise ValueError("catalog chapter is invalid")
            raw_images = raw_chapter.get("images")
            if not isinstance(raw_images, list):
                raise ValueError("catalog image list is invalid")
            images = tuple(
                ImageResource(
                    required_text(raw_image.get("url"), message="catalog image URL is missing"),
                    index=index,
                    referer=url,
                    image_id=required_text(raw_image.get("id"), message="catalog image ID is missing"),
                )
                for index, raw_image in enumerate(raw_images, start=1)
                if isinstance(raw_image, dict)
            )
            if len(images) != len(raw_images):
                raise ValueError("catalog image is invalid")
            chapters.append(
                Chapter(
                    number,
                    str(raw_chapter.get("title") or title),
                    images=images,
                    chapter_id=required_text(raw_chapter.get("id"), message="catalog chapter ID is missing"),
                )
            )
        return DownloadManifest(
            title,
            tuple(chapters),
            content_id=required_text(work.get("id"), message="catalog work ID is missing"),
            revision=str(work.get("revision") or "") or None,
            metadata={"pattern": "chaptered-catalog"},
        )

    async def create_image_request(self, image: ImageResource, context: PluginExecutionContext) -> RequestSpec:
        return RequestSpec(image.url, referer=image.referer)

    async def recover_image_request(
        self, image: ImageResource, failed: RequestSpec, response: RequestResponse, context: PluginExecutionContext
    ) -> RequestSpec | None:
        return None

    def auth_flow(self, context: PluginExecutionContext):
        return None

    async def transform_image(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact:
        return artifact

    async def check_updates(self, url: str, context: PluginExecutionContext) -> UpdateSnapshot:
        response = await context.requests.execute(
            RequestSpec(f"{origin(url)}/api/updates", headers={"Accept": "application/json"})
        )
        payload = response_json(response, message="catalog update response is invalid")
        works = payload.get("works")
        if not isinstance(works, list):
            raise ValueError("catalog updates are invalid")
        candidates = tuple(
            UpdateCandidate(
                required_text(work.get("url"), message="catalog update URL is missing"),
                content_id=required_text(work.get("id"), message="catalog update ID is missing"),
                revision=str(work.get("revision") or "") or None,
            )
            for work in works
            if isinstance(work, dict)
        )
        if len(candidates) != len(works):
            raise ValueError("catalog update is invalid")
        return UpdateSnapshot(url, candidates, datetime.now(UTC))
