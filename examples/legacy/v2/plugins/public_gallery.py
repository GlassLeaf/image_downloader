"""Public HTML gallery: inspect HTML and centralize Referer/Origin per image."""

from __future__ import annotations

import re
from html import unescape
from urllib.parse import urljoin, urlparse

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

from ._support import origin


class PublicGalleryPlugin:
    descriptor = PluginDescriptor("example.public-gallery", priority=10)

    def matches(self, url: str) -> bool:
        parsed = urlparse(url)
        return parsed.scheme in {"http", "https"} and parsed.hostname == "public-gallery.example.test"

    async def inspect(self, url: str, context: PluginExecutionContext) -> DownloadManifest:
        response = await context.requests.execute(RequestSpec(url, headers={"Accept": "text/html"}))
        html = response.body.decode("utf-8", errors="replace")
        title = unescape(_first(r"<title[^>]*>\s*(.*?)\s*</title>", html, "Untitled gallery"))
        images: list[ImageResource] = []
        for tag in re.findall(r"<img\b[^>]*>", html, flags=re.IGNORECASE):
            source = _attribute(tag, "src")
            if source is None:
                continue
            images.append(
                ImageResource(
                    urljoin(url, source),
                    index=len(images) + 1,
                    referer=url,
                    image_id=_attribute(tag, "data-image-id"),
                )
            )
        return DownloadManifest(title, (Chapter(1, title, images=tuple(images)),), metadata={"pattern": "public-html"})

    async def create_image_request(self, image: ImageResource, context: PluginExecutionContext) -> RequestSpec:
        return RequestSpec(
            image.url, headers={**image.headers, "Origin": origin(image.referer or image.url)}, referer=image.referer
        )

    async def recover_image_request(
        self, image: ImageResource, failed: RequestSpec, response: RequestResponse, context: PluginExecutionContext
    ) -> RequestSpec | None:
        return None

    def auth_flow(self, context: PluginExecutionContext):
        return None

    async def transform_image(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact:
        return artifact


def _first(pattern: str, text: str, fallback: str) -> str:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    return match.group(1) if match else fallback


def _attribute(tag: str, name: str) -> str | None:
    match = re.search(rf"\b{re.escape(name)}=['\"]([^'\"]+)['\"]", tag, flags=re.IGNORECASE)
    return match.group(1) if match else None
