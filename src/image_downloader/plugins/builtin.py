"""Trusted core-supplied v3 fallback plugin."""

from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from ..models import (
    Chapter,
    DownloadManifest,
    ImageArtifact,
    ImageResource,
    RequestResponse,
    RequestSpec,
)
from ..ports import PluginExecutionContext, TransformContext


class _ImageParser(HTMLParser):
    def __init__(self, source_url: str) -> None:
        super().__init__()
        self.source_url = source_url
        self.title = "download"
        self.images: list[ImageResource] = []
        self._in_title = False
        self._title: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "title":
            self._in_title = True
        if tag.lower() == "img" and values.get("src"):
            self.images.append(
                ImageResource(
                    urljoin(self.source_url, values["src"]),
                    len(self.images) + 1,
                    referer=self.source_url,
                    image_id=values.get("data-image-id") or None,
                )
            )

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False
            value = "".join(self._title).strip()
            if value:
                self.title = value

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title.append(data)


class GenericHtmlPlugin:
    """Built-in fallback for ordinary public HTML galleries."""

    def validate_config(self, config, app_settings) -> None:
        # The static built-in manifest is authoritative and this fallback has
        # no private user configuration.
        return None

    def matches(self, url: str) -> bool:
        return urlparse(url).scheme in {"http", "https"}

    async def inspect(self, url: str, context: PluginExecutionContext) -> DownloadManifest:
        response = await context.requests.execute(RequestSpec(url))
        parser = _ImageParser(url)
        parser.feed(response.body.decode("utf-8", errors="replace"))
        return DownloadManifest(parser.title, (Chapter(1, parser.title, images=tuple(parser.images)),))

    async def create_image_request(self, image: ImageResource, context: PluginExecutionContext) -> RequestSpec:
        return RequestSpec(image.url, headers=image.headers, referer=image.referer)

    async def recover_image_request(
        self, image: ImageResource, failed: RequestSpec, response: RequestResponse, context: PluginExecutionContext
    ) -> RequestSpec | None:
        return None

    def auth_flow(self, context: PluginExecutionContext):
        return None

    async def transform_image(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact:
        return artifact
