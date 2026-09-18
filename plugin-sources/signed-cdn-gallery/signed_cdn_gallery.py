"""v3 sample: mint a replacement signed image URL after an HTTP failure."""

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


def _json(body, message):
    try:
        value = json.loads(body)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PluginError(message) from exc
    if not isinstance(value, Mapping):
        raise PluginError(message)
    return value


class SignedCdnGalleryPlugin:
    def validate_config(self, config: Mapping[str, object], app_settings: Mapping[str, object]) -> None:
        del app_settings
        if config:
            raise ValueError("signed CDN gallery does not accept configuration")

    def matches(self, url):
        p = urlparse(url)
        return p.scheme in {"http", "https"} and p.hostname == "signed-cdn.example.test"

    async def inspect(self, url, context):
        p = urlparse(url)
        origin = f"{p.scheme}://{p.netloc}"
        work = p.path.rstrip("/").rsplit("/", 1)[-1]
        response = await context.requests.execute(
            RequestSpec(f"{origin}/api/works/{work}/images", headers={"Accept": "application/json"})
        )
        payload = _json(response.body, "signed CDN image list is invalid")
        raw = payload.get("images")
        if not isinstance(raw, list):
            raise ValueError("signed CDN image list is invalid")
        images = tuple(
            ImageResource(
                f"{origin}/placeholder/{_text(item.get('id'), 'signed CDN image ID is missing')}",
                index=index,
                referer=url,
                image_id=_text(item.get("id"), "signed CDN image ID is missing"),
            )
            for index, item in enumerate(raw, 1)
            if isinstance(item, Mapping)
        )
        if len(images) != len(raw):
            raise ValueError("signed CDN image is invalid")
        title = str(payload.get("title") or "Signed CDN gallery")
        return DownloadManifest(
            title,
            (Chapter(1, title, images=images),),
            content_id=_text(payload.get("id"), "signed CDN work ID is missing"),
        )

    async def _signed_request(self, image, context):
        if image.image_id is None:
            raise ValueError("signed CDN image ID is missing")
        p = urlparse(image.referer or image.url)
        response = await context.requests.execute(
            RequestSpec(
                f"{p.scheme}://{p.netloc}/api/images/{image.image_id}/signed", headers={"Accept": "application/json"}
            )
        )
        return RequestSpec(
            _text(_json(response.body, "signed CDN URL response is invalid").get("url"), "signed CDN URL is missing"),
            referer=image.referer,
        )

    async def create_image_request(self, image, context):
        return await self._signed_request(image, context)

    async def recover_image_request(self, image, failed, response, context):
        del failed
        return (
            await self._signed_request(image, context)
            if response.status >= 400 and image.image_id is not None
            else None
        )

    def auth_flow(self, context):
        del context
        return None

    async def transform_image(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact:
        del context
        return artifact
