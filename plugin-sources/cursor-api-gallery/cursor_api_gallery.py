"""v3 sample: page through a cursor API using the api_token secret."""

from __future__ import annotations

import json
from collections.abc import Mapping
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


def _json(body):
    try:
        value = json.loads(body)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PluginError("cursor gallery response is invalid") from exc
    if not isinstance(value, Mapping):
        raise PluginError("cursor gallery response is invalid")
    return value


class CursorApiGalleryPlugin:
    def validate_config(self, config: Mapping[str, object], app_settings: Mapping[str, object]) -> None:
        del app_settings
        if config:
            raise ValueError("cursor API gallery does not accept configuration")

    def matches(self, url):
        p = urlparse(url)
        return p.scheme in {"http", "https"} and p.hostname == "cursor-api.example.test"

    async def inspect(self, url, context):
        p = urlparse(url)
        item = p.path.rstrip("/").rsplit("/", 1)[-1]
        if not item:
            raise PluginError("example URL is missing its resource ID")
        api = f"{p.scheme}://{p.netloc}/api/galleries/{item}"
        cursor = None
        seen = set()
        images = []
        title = "Cursor gallery"
        content_id = None
        while True:
            if cursor is not None:
                if cursor in seen:
                    raise ValueError("cursor API repeated a cursor")
                seen.add(cursor)
            response = await context.requests.execute(
                RequestSpec(
                    api,
                    method="POST",
                    headers={"Accept": "application/json", "X-Api-Key": context.secrets.get("api_token")},
                    json={"cursor": cursor},
                )
            )
            gallery = _json(response.body).get("gallery")
            if not isinstance(gallery, Mapping) or not isinstance(gallery.get("images"), list):
                raise ValueError("cursor gallery response is invalid")
            title = _text(gallery.get("title"), "cursor gallery title is missing")
            content_id = _text(gallery.get("id"), "cursor gallery ID is missing")
            for raw in gallery["images"]:
                if not isinstance(raw, Mapping):
                    raise ValueError("cursor image is invalid")
                images.append(
                    ImageResource(
                        _text(raw.get("url"), "cursor image URL is missing"),
                        index=len(images) + 1,
                        referer=url,
                        image_id=_text(raw.get("id"), "cursor image ID is missing"),
                    )
                )
            if gallery.get("next_cursor") is None:
                break
            cursor = _text(gallery.get("next_cursor"), "cursor is invalid")
        return DownloadManifest(title, (Chapter(1, title, images=tuple(images)),), content_id=content_id)

    async def create_image_request(self, image, context):
        del context
        return RequestSpec(image.url, referer=image.referer)

    async def recover_image_request(self, image, failed, response, context):
        del image, failed, response, context
        return None

    def auth_flow(self, context):
        del context
        return None

    async def transform_image(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact:
        del context
        return artifact
