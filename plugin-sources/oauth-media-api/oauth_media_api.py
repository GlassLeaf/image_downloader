"""v3 sample: refresh an OAuth bearer token after a JSON auth error."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from urllib.parse import urlparse

from image_downloader import (
    AuthContext,
    AuthDecision,
    AuthenticationError,
    AuthFlow,
    AuthLease,
    BaseAuthFlow,
    Chapter,
    DownloadManifest,
    ImageArtifact,
    ImageResource,
    PluginError,
    PluginExecutionContext,
    RequestResponse,
    RequestSpec,
    TransformContext,
)


def _text(value: object, message: str) -> str:
    if not isinstance(value, str) or not value:
        raise PluginError(message)
    return value


def _json(body: bytes, message: str) -> Mapping[str, object]:
    try:
        value = json.loads(body)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PluginError(message) from exc
    if not isinstance(value, Mapping):
        raise PluginError(message)
    return value


class _OAuthFlow(BaseAuthFlow):
    async def initialize(self, auth_context: AuthContext) -> AuthLease:
        self.access_token = auth_context.credentials.get("access_token")
        return AuthLease()

    def classify_response(self, request: RequestSpec, response: RequestResponse) -> AuthDecision:
        if response.status == 200 and b'"error":"expired_token"' in response.body.replace(b" ", b""):
            return AuthDecision.AUTH_FAILURE
        return super().classify_response(request, response)

    def apply(self, request: RequestSpec) -> RequestSpec:
        return replace(request, headers={**request.headers, "Authorization": f"Bearer {self.access_token}"})

    async def login(self, auth_context: AuthContext) -> AuthLease:
        origin = auth_context.endpoints["operation"].origin
        token = await auth_context.requests.execute(
            RequestSpec(
                f"{origin}/oauth/token",
                method="POST",
                form={"grant_type": "refresh_token", "refresh_token": auth_context.credentials.get("refresh_token")},
            )
        )
        if token.status >= 400:
            raise AuthenticationError("OAuth token refresh failed")
        data = _json(token.body, "OAuth token response is invalid")
        self.access_token = _text(data.get("access_token"), "OAuth token response is invalid")
        return AuthLease(data.get("expires_in"))


class OAuthMediaApiPlugin:
    def validate_config(self, config: Mapping[str, object], app_settings: Mapping[str, object]) -> None:
        del app_settings
        if config:
            raise ValueError("OAuth media API does not accept configuration")

    def matches(self, url: str) -> bool:
        p = urlparse(url)
        return p.scheme in {"http", "https"} and p.hostname == "oauth-media.example.test"

    async def inspect(self, url: str, context: PluginExecutionContext) -> DownloadManifest:
        p = urlparse(url)
        response = await context.requests.execute(
            RequestSpec(
                f"{p.scheme}://{p.netloc}/api/media/{p.path.rstrip('/').rsplit('/', 1)[-1]}",
                headers={"Accept": "application/json"},
            )
        )
        media = _json(response.body, "OAuth media response is invalid").get("media")
        if not isinstance(media, Mapping) or not isinstance(media.get("images"), list):
            raise ValueError("OAuth media response is invalid")
        title = _text(media.get("title"), "OAuth media title is missing")
        images = tuple(
            ImageResource(
                _text(item.get("url"), "OAuth image URL is missing"),
                index=index,
                referer=url,
                image_id=_text(item.get("id"), "OAuth image ID is missing"),
            )
            for index, item in enumerate(media["images"], 1)
            if isinstance(item, Mapping)
        )
        if len(images) != len(media["images"]):
            raise ValueError("OAuth image is invalid")
        return DownloadManifest(
            title, (Chapter(1, title, images=images),), content_id=_text(media.get("id"), "OAuth media ID is missing")
        )

    async def create_image_request(self, image: ImageResource, context: PluginExecutionContext) -> RequestSpec:
        del context
        return RequestSpec(image.url, referer=image.referer)

    async def recover_image_request(
        self,
        image: ImageResource,
        failed: RequestSpec,
        response: RequestResponse,
        context: PluginExecutionContext,
    ) -> RequestSpec | None:
        del image, failed, response, context
        return None

    def auth_flow(self, context: PluginExecutionContext) -> AuthFlow | None:
        return _OAuthFlow()

    def transform_image(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact:
        del context
        return artifact
