"""Cursor API: read every fixed page through RequestPort and a secret reference."""

from __future__ import annotations

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
)

from ._support import origin, path_id, required_text, response_json


class CursorApiGalleryPlugin:
    descriptor = PluginDescriptor("example.cursor-api-gallery", priority=20)

    def matches(self, url: str) -> bool:
        parsed = urlparse(url)
        return parsed.scheme in {"http", "https"} and parsed.hostname == "cursor-api.example.test"

    async def inspect(self, url: str, context: PluginExecutionContext) -> DownloadManifest:
        api = f"{origin(url)}/api/galleries/{path_id(url)}"
        cursor: str | None = None
        seen: set[str] = set()
        images: list[ImageResource] = []
        title = "Cursor gallery"
        gallery_id: str | None = None
        while True:
            if cursor is not None and cursor in seen:
                raise ValueError("cursor API repeated a cursor")
            if cursor is not None:
                seen.add(cursor)
            response = await context.requests.execute(
                RequestSpec(
                    api,
                    method="POST",
                    headers={"Accept": "application/json", "X-Api-Key": context.secrets.get("api_token")},
                    json={"cursor": cursor},
                )
            )
            payload = response_json(response, message="cursor gallery response is invalid")
            gallery = payload.get("gallery")
            if not isinstance(gallery, dict):
                raise ValueError("cursor gallery response is invalid")
            title = required_text(gallery.get("title"), message="cursor gallery title is missing")
            gallery_id = required_text(gallery.get("id"), message="cursor gallery ID is missing")
            page_images = gallery.get("images")
            if not isinstance(page_images, list):
                raise ValueError("cursor image list is invalid")
            for raw_image in page_images:
                if not isinstance(raw_image, dict):
                    raise ValueError("cursor image is invalid")
                images.append(
                    ImageResource(
                        required_text(raw_image.get("url"), message="cursor image URL is missing"),
                        index=len(images) + 1,
                        referer=url,
                        image_id=required_text(raw_image.get("id"), message="cursor image ID is missing"),
                    )
                )
            next_cursor = gallery.get("next_cursor")
            if next_cursor is None:
                break
            cursor = required_text(next_cursor, message="cursor is invalid")
        return DownloadManifest(title, (Chapter(1, title, images=tuple(images)),), content_id=gallery_id)

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
