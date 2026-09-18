"""Generic HTML site plugin that extracts ``src`` from a user CSS selector."""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urljoin, urlparse

from image_downloader import (
    Chapter,
    DownloadManifest,
    ImageArtifact,
    ImageResource,
    PluginExecutionContext,
    RequestResponse,
    RequestSpec,
    TransformContext,
)

from .vendor.bs4 import BeautifulSoup
from .vendor.soupsieve import SelectorSyntaxError
from .vendor.soupsieve import compile as compile_selector


class GenericCssSelector:
    """Download image URLs from elements selected with a CSS selector."""

    def validate_config(self, config: Mapping[str, object], app_settings: Mapping[str, object]) -> None:
        del app_settings
        if set(config) != {"selector"}:
            raise ValueError("GenericCssSelector config must contain only selector")
        selector = config.get("selector")
        if not isinstance(selector, str) or not selector.strip():
            raise ValueError("GenericCssSelector selector must be a non-empty string")
        try:
            compile_selector(selector)
        except SelectorSyntaxError as exc:
            raise ValueError("GenericCssSelector selector is invalid") from exc

    def matches(self, url: str) -> bool:
        return urlparse(url).scheme in {"http", "https"}

    async def inspect(self, url: str, context: PluginExecutionContext) -> DownloadManifest:
        selector = str(context.config["selector"])
        response = await context.requests.execute(RequestSpec(url, headers={"Accept": "text/html"}))
        soup = BeautifulSoup(response.body, "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else "download"
        images: list[ImageResource] = []
        seen: set[str] = set()
        for element in soup.select(selector):
            source = element.get("src")
            if not isinstance(source, str) or not source.strip():
                continue
            resolved = urljoin(url, source.strip())
            if urlparse(resolved).scheme not in {"http", "https"} or resolved in seen:
                continue
            seen.add(resolved)
            images.append(ImageResource(resolved, index=len(images) + 1, referer=url))
        return DownloadManifest(title, (Chapter(1, title, images=tuple(images)),), metadata={"pattern": "generic-css-selector"})

    async def create_image_request(self, image: ImageResource, context: PluginExecutionContext) -> RequestSpec:
        del context
        return RequestSpec(image.url, headers={"Accept": "image/*"}, referer=image.referer)

    async def recover_image_request(
        self, image: ImageResource, failed: RequestSpec, response: RequestResponse, context: PluginExecutionContext
    ) -> RequestSpec | None:
        del image, failed, response, context
        return None

    def auth_flow(self, context: PluginExecutionContext):
        del context
        return None

    async def transform_image(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact:
        del context
        return artifact
