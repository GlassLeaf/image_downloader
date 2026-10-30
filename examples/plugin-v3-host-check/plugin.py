"""Unsigned host-matching template with no network or filesystem operations."""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlsplit

from image_downloader import (
    AuthFlow,
    DownloadManifest,
    ImageArtifact,
    ImageResource,
    PluginExecutionContext,
    RequestResponse,
    RequestSpec,
    TransformContext,
)

# Replace these exact host names when adapting the template. Subdomains are
# intentionally excluded unless explicitly listed here.
SUPPORTED_HOSTS = frozenset({"example.test", "www.example.test"})


class HostCheckPlugin:
    def validate_config(self, config: Mapping[str, object], app_settings: Mapping[str, object]) -> None:
        if config:
            raise ValueError("HostCheckPlugin does not accept configuration")

    def matches(self, url: str) -> bool:
        try:
            parsed = urlsplit(url)
            return parsed.scheme in {"http", "https"} and parsed.hostname in SUPPORTED_HOSTS
        except ValueError:
            return False

    async def inspect(self, url: str, context: PluginExecutionContext) -> DownloadManifest:
        return DownloadManifest(title="Host check only", chapters=(), allow_empty_chapters=True)

    async def create_image_request(self, image: ImageResource, context: PluginExecutionContext) -> RequestSpec:
        raise NotImplementedError("HostCheckPlugin does not download images")

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
