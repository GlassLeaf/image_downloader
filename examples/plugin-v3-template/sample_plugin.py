"""Template only: sign the matching manifest before installing it."""

from __future__ import annotations

from collections.abc import Mapping

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


class SamplePlugin:
    def validate_config(self, config: Mapping[str, object], app_settings: Mapping[str, object]) -> None:
        return None

    def matches(self, url: str) -> bool:
        return False

    async def inspect(self, url: str, context: PluginExecutionContext) -> DownloadManifest:
        raise NotImplementedError

    async def create_image_request(self, image: ImageResource, context: PluginExecutionContext) -> RequestSpec:
        return RequestSpec(image.url)

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
