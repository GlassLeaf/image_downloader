"""v3 sample: refresh an OAuth bearer token after a JSON auth error."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from urllib.parse import urlparse

from image_downloader import (
    Chapter,
    DownloadManifest,
    ImageArtifact,
    ImageResource,
    PluginError,
    RequestSpec,
    TransformContext,
)


def _text(value, message):
    if not isinstance(value, str) or not value:
        raise PluginError(message)
    return value


def _json(body, message):
    try:
        value = json.loads(body)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PluginError(message) from exc
    if not isinstance(value, Mapping):
        raise PluginError(message)
    return value


class _OAuthFlow:
    def __init__(self, context):
        self.context = context
        self.access_token = context.secrets.get("access_token")

    def is_auth_failure(self, request, response):
        return response.status == 200 and b'"error":"expired_token"' in response.body.replace(b" ", b"")

    async def apply(self, request):
        return replace(request, headers={**request.headers, "Authorization": f"Bearer {self.access_token}"})

    async def refresh(self, failed, response):
        p = urlparse(failed.url)
        token = await self.context.requests.execute(
            RequestSpec(
                f"{p.scheme}://{p.netloc}/oauth/token",
                method="POST",
                form={"grant_type": "refresh_token", "refresh_token": self.context.secrets.get("refresh_token")},
                auth_required=False,
            )
        )
        self.access_token = _text(
            _json(token.body, "OAuth token response is invalid").get("access_token"), "OAuth token response is invalid"
        )
        return failed


class OAuthMediaApiPlugin:
    def validate_config(self, config: Mapping[str, object], app_settings: Mapping[str, object]) -> None:
        del app_settings
        if config:
            raise ValueError("OAuth media API does not accept configuration")

    def matches(self, url):
        p = urlparse(url)
        return p.scheme in {"http", "https"} and p.hostname == "oauth-media.example.test"

    async def inspect(self, url, context):
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

    async def create_image_request(self, image, context):
        del context
        return RequestSpec(image.url, referer=image.referer)

    async def recover_image_request(self, image, failed, response, context):
        del image, failed, response, context
        return None

    def auth_flow(self, context):
        return _OAuthFlow(context)

    async def transform_image(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact:
        del context
        return artifact
