"""v3 sample: inspect a public HTML gallery and set image Origin/Referer."""

from __future__ import annotations

import re
from collections.abc import Mapping
from html import unescape
from urllib.parse import urljoin, urlparse

from image_downloader import Chapter, DownloadManifest, ImageArtifact, ImageResource, RequestSpec, TransformContext


class PublicGalleryPlugin:
    def validate_config(self, config: Mapping[str, object], app_settings: Mapping[str, object]) -> None:
        del app_settings
        if config:
            raise ValueError("public gallery does not accept configuration")

    def matches(self, url: str) -> bool:
        p = urlparse(url)
        return p.scheme in {"http", "https"} and p.hostname == "public-gallery.example.test"

    async def inspect(self, url, context):
        html = (await context.requests.execute(RequestSpec(url, headers={"Accept": "text/html"}))).body.decode(
            "utf-8", "replace"
        )
        title = unescape(_first(r"<title[^>]*>\s*(.*?)\s*</title>", html, "Untitled gallery"))
        images = []
        for tag in re.findall(r"<img\b[^>]*>", html, flags=re.I):
            if source := _attribute(tag, "src"):
                images.append(
                    ImageResource(
                        urljoin(url, source),
                        index=len(images) + 1,
                        referer=url,
                        image_id=_attribute(tag, "data-image-id"),
                    )
                )
        return DownloadManifest(title, (Chapter(1, title, images=tuple(images)),), metadata={"pattern": "public-html"})

    async def create_image_request(self, image, context):
        del context
        p = urlparse(image.referer or image.url)
        return RequestSpec(
            image.url, headers={**image.headers, "Origin": f"{p.scheme}://{p.netloc}"}, referer=image.referer
        )

    async def recover_image_request(self, image, failed, response, context):
        del image, failed, response, context
        return None

    def auth_flow(self, context):
        del context
        return None

    async def transform_image(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact:
        del context
        return artifact


def _first(pattern, text, fallback):
    match = re.search(pattern, text, flags=re.I | re.S)
    return match.group(1) if match else fallback


def _attribute(tag, name):
    match = re.search(rf"\b{re.escape(name)}=['\"]([^'\"]+)['\"]", tag, flags=re.I)
    return match.group(1) if match else None
