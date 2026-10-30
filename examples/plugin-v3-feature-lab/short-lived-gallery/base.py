"""Copied into each lab package: the package has no dependency on the lab runner."""

from __future__ import annotations

import json
from collections.abc import Mapping
from urllib.parse import urlsplit

from image_downloader import (
    AuthFlow,
    Chapter,
    DownloadManifest,
    ImageArtifact,
    ImageResource,
    PluginExecutionContext,
    RequestResponse,
    RequestSpec,
    TransformContext,
)


def local_origin(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("lab origin must be a loopback HTTP origin")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
        or not parsed.port
    ):
        raise ValueError("lab origin must be a loopback HTTP origin")
    return value


class BaseGallery:
    slug = ""

    def validate_config(self, config: Mapping[str, object], app_settings: Mapping[str, object]) -> None:
        if set(config) != {"origin", "asset_origin"}:
            raise ValueError("lab configuration requires origin and asset_origin")
        local_origin(config["origin"])
        local_origin(config["asset_origin"])

    def matches(self, url: str) -> bool:
        try:
            parsed = urlsplit(url)
            return (
                parsed.scheme == "http"
                and parsed.hostname in {"127.0.0.1", "localhost"}
                and parsed.path.startswith(f"/samples/{self.slug}/")
            )
        except ValueError:
            return False

    async def inspect(self, url: str, context: PluginExecutionContext) -> DownloadManifest:
        response = await context.requests.execute(RequestSpec(url))
        document = json.loads(response.body)
        chapters = tuple(
            Chapter(
                number,
                chapter["title"],
                chapter_id=chapter["id"],
                images=tuple(
                    ImageResource(image["url"], index=index, image_id=image["id"], referer=url)
                    for index, image in enumerate(chapter["images"], 1)
                ),
            )
            for number, chapter in enumerate(document["chapters"], 1)
        )
        return DownloadManifest(document["title"], chapters, content_id=document["id"])

    async def create_image_request(self, image: ImageResource, context: PluginExecutionContext) -> RequestSpec:
        return RequestSpec(image.url, referer=image.referer)

    async def recover_image_request(
        self,
        image: ImageResource,
        failed: RequestSpec,
        response: RequestResponse,
        context: PluginExecutionContext,
    ) -> RequestSpec | None:
        return None

    def auth_flow(self, context: PluginExecutionContext) -> AuthFlow | None:
        return None

    async def transform_image(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact:
        return artifact
