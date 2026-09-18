"""Signed CDN: retain a stable image ID and mint a replacement request once."""

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


class SignedCdnGalleryPlugin:
    descriptor = PluginDescriptor("example.signed-cdn-gallery", priority=20)

    def matches(self, url: str) -> bool:
        parsed = urlparse(url)
        return parsed.scheme in {"http", "https"} and parsed.hostname == "signed-cdn.example.test"

    async def inspect(self, url: str, context: PluginExecutionContext) -> DownloadManifest:
        response = await context.requests.execute(
            RequestSpec(f"{origin(url)}/api/works/{path_id(url)}/images", headers={"Accept": "application/json"})
        )
        payload = response_json(response, message="signed CDN image list is invalid")
        images_payload = payload.get("images")
        if not isinstance(images_payload, list):
            raise ValueError("signed CDN image list is invalid")
        images = tuple(
            ImageResource(
                f"{origin(url)}/placeholder/{required_text(item.get('id'), message='signed CDN image ID is missing')}",
                index=index,
                referer=url,
                image_id=required_text(item.get("id"), message="signed CDN image ID is missing"),
            )
            for index, item in enumerate(images_payload, start=1)
            if isinstance(item, dict)
        )
        if len(images) != len(images_payload):
            raise ValueError("signed CDN image is invalid")
        title = str(payload.get("title") or "Signed CDN gallery")
        return DownloadManifest(
            title,
            (Chapter(1, title, images=images),),
            content_id=required_text(payload.get("id"), message="signed CDN work ID is missing"),
        )

    async def create_image_request(self, image: ImageResource, context: PluginExecutionContext) -> RequestSpec:
        return await self._signed_request(image, context)

    async def recover_image_request(
        self, image: ImageResource, failed: RequestSpec, response: RequestResponse, context: PluginExecutionContext
    ) -> RequestSpec | None:
        if response.status < 400 or image.image_id is None:
            return None
        return await self._signed_request(image, context)

    async def _signed_request(self, image: ImageResource, context: PluginExecutionContext) -> RequestSpec:
        if image.image_id is None:
            raise ValueError("signed CDN image ID is missing")
        response = await context.requests.execute(
            RequestSpec(
                f"{origin(image.referer or image.url)}/api/images/{image.image_id}/signed",
                headers={"Accept": "application/json"},
            )
        )
        payload = response_json(response, message="signed CDN URL response is invalid")
        return RequestSpec(
            required_text(payload.get("url"), message="signed CDN URL is missing"), referer=image.referer
        )

    def auth_flow(self, context: PluginExecutionContext):
        return None

    async def transform_image(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact:
        return artifact
