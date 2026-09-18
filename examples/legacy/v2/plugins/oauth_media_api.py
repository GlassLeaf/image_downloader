"""OAuth media API: treat an HTTP 200 JSON auth error as an auth failure."""

from __future__ import annotations

from dataclasses import replace
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


class _OAuthFlow:
    def __init__(self, context: PluginExecutionContext) -> None:
        self.context = context
        self.access_token = context.secrets.get("access_token")

    def is_auth_failure(self, request: RequestSpec, response: RequestResponse) -> bool:
        return response.status == 200 and b'"error":"expired_token"' in response.body.replace(b" ", b"")

    async def apply(self, request: RequestSpec) -> RequestSpec:
        return replace(request, headers={**request.headers, "Authorization": f"Bearer {self.access_token}"})

    async def refresh(self, failed: RequestSpec, response: RequestResponse) -> RequestSpec | None:
        token = await self.context.requests.execute(
            RequestSpec(
                f"{origin(failed.url)}/oauth/token",
                method="POST",
                form={"grant_type": "refresh_token", "refresh_token": self.context.secrets.get("refresh_token")},
                auth_required=False,
            )
        )
        payload = response_json(token, message="OAuth token response is invalid")
        self.access_token = required_text(payload.get("access_token"), message="OAuth token response is invalid")
        return failed


class OAuthMediaApiPlugin:
    descriptor = PluginDescriptor("example.oauth-media-api", priority=20)

    def matches(self, url: str) -> bool:
        parsed = urlparse(url)
        return parsed.scheme in {"http", "https"} and parsed.hostname == "oauth-media.example.test"

    async def inspect(self, url: str, context: PluginExecutionContext) -> DownloadManifest:
        response = await context.requests.execute(
            RequestSpec(f"{origin(url)}/api/media/{path_id(url)}", headers={"Accept": "application/json"})
        )
        payload = response_json(response, message="OAuth media response is invalid")
        media = payload.get("media")
        if not isinstance(media, dict) or not isinstance(media.get("images"), list):
            raise ValueError("OAuth media response is invalid")
        title = required_text(media.get("title"), message="OAuth media title is missing")
        images = tuple(
            ImageResource(
                required_text(item.get("url"), message="OAuth image URL is missing"),
                index=index,
                referer=url,
                image_id=required_text(item.get("id"), message="OAuth image ID is missing"),
            )
            for index, item in enumerate(media["images"], start=1)
            if isinstance(item, dict)
        )
        if len(images) != len(media["images"]):
            raise ValueError("OAuth image is invalid")
        return DownloadManifest(
            title,
            (Chapter(1, title, images=images),),
            content_id=required_text(media.get("id"), message="OAuth media ID is missing"),
        )

    async def create_image_request(self, image: ImageResource, context: PluginExecutionContext) -> RequestSpec:
        return RequestSpec(image.url, referer=image.referer)

    async def recover_image_request(
        self, image: ImageResource, failed: RequestSpec, response: RequestResponse, context: PluginExecutionContext
    ) -> RequestSpec | None:
        return None

    def auth_flow(self, context: PluginExecutionContext) -> _OAuthFlow:
        return _OAuthFlow(context)

    async def transform_image(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact:
        return artifact
