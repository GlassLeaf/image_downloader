"""v3 sample: login through a CSRF form using external credential secrets."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import replace
from urllib.parse import urlparse

from image_downloader import (
    AuthenticationError,
    Chapter,
    DownloadManifest,
    ImageArtifact,
    ImageResource,
    RequestSpec,
    TransformContext,
)


class _CsrfFlow:
    def __init__(self, context):
        self.context = context

    def is_auth_failure(self, request, response):
        return response.status == 200 and b"login-required" in response.body.lower()

    async def apply(self, request):
        return request

    async def refresh(self, failed, response):
        p = urlparse(failed.url)
        origin = f"{p.scheme}://{p.netloc}"
        page = await self.context.requests.execute(RequestSpec(f"{origin}/login", auth_required=False))
        match = re.search(r"name=['\"]csrf['\"]\s+value=['\"]([^'\"]+)['\"]", page.body.decode("utf-8", "replace"))
        if match is None:
            raise AuthenticationError("login form did not contain a CSRF token")
        session = await self.context.requests.execute(
            RequestSpec(
                f"{origin}/session",
                method="POST",
                form={
                    "username": self.context.secrets.get("username"),
                    "password": self.context.secrets.get("password"),
                    "csrf": match.group(1),
                },
                auth_required=False,
            )
        )
        if session.status >= 400:
            raise AuthenticationError("login session request failed")
        return replace(failed)


class CsrfLoginGalleryPlugin:
    def validate_config(self, config: Mapping[str, object], app_settings: Mapping[str, object]) -> None:
        del app_settings
        if config:
            raise ValueError("CSRF login gallery does not accept configuration")

    def matches(self, url):
        p = urlparse(url)
        return p.scheme in {"http", "https"} and p.hostname == "csrf-login.example.test"

    async def inspect(self, url, context):
        html = (await context.requests.execute(RequestSpec(url, headers={"Accept": "text/html"}))).body.decode(
            "utf-8", "replace"
        )
        title_match = re.search(r"<title[^>]*>\s*(.*?)\s*</title>", html, re.I | re.S)
        title = title_match.group(1) if title_match else "Members gallery"
        images = tuple(
            ImageResource(source, index=index, referer=url, image_id=image_id)
            for index, (image_id, source) in enumerate(
                re.findall(r"data-id=['\"]([^'\"]+)['\"][^>]*src=['\"]([^'\"]+)['\"]", html), 1
            )
        )
        return DownloadManifest(
            title, (Chapter(1, title, images=images),), content_id=urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
        )

    async def create_image_request(self, image, context):
        del context
        return RequestSpec(image.url, referer=image.referer)

    async def recover_image_request(self, image, failed, response, context):
        del image, failed, response, context
        return None

    def auth_flow(self, context):
        return _CsrfFlow(context)

    async def transform_image(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact:
        del context
        return artifact
