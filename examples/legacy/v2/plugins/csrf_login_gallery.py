"""CSRF form login: keep login traffic inside AuthFlow.refresh()."""

from __future__ import annotations

import re
from dataclasses import replace
from urllib.parse import urlparse

from image_downloader import (
    AuthenticationError,
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

from ._support import origin, path_id


class _CsrfFlow:
    def __init__(self, context: PluginExecutionContext) -> None:
        self.context = context

    def is_auth_failure(self, request: RequestSpec, response: RequestResponse) -> bool:
        return response.status == 200 and b"login-required" in response.body.lower()

    async def apply(self, request: RequestSpec) -> RequestSpec:
        return request

    async def refresh(self, failed: RequestSpec, response: RequestResponse) -> RequestSpec | None:
        login_url = f"{origin(failed.url)}/login"
        login_page = await self.context.requests.execute(RequestSpec(login_url, auth_required=False))
        match = re.search(
            r"name=['\"]csrf['\"]\s+value=['\"]([^'\"]+)['\"]", login_page.body.decode("utf-8", "replace")
        )
        if match is None:
            raise AuthenticationError("login form did not contain a CSRF token")
        session = await self.context.requests.execute(
            RequestSpec(
                f"{origin(failed.url)}/session",
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
    descriptor = PluginDescriptor("example.csrf-login-gallery", priority=20)

    def matches(self, url: str) -> bool:
        parsed = urlparse(url)
        return parsed.scheme in {"http", "https"} and parsed.hostname == "csrf-login.example.test"

    async def inspect(self, url: str, context: PluginExecutionContext) -> DownloadManifest:
        response = await context.requests.execute(RequestSpec(url, headers={"Accept": "text/html"}))
        html = response.body.decode("utf-8", errors="replace")
        title_match = re.search(r"<title[^>]*>\s*(.*?)\s*</title>", html, re.IGNORECASE | re.DOTALL)
        title = title_match.group(1) if title_match else "Members gallery"
        images = tuple(
            ImageResource(source, index=index, referer=url, image_id=image_id)
            for index, (image_id, source) in enumerate(
                re.findall(r"data-id=['\"]([^'\"]+)['\"][^>]*src=['\"]([^'\"]+)['\"]", html), start=1
            )
        )
        return DownloadManifest(title, (Chapter(1, title, images=images),), content_id=path_id(url))

    async def create_image_request(self, image: ImageResource, context: PluginExecutionContext) -> RequestSpec:
        return RequestSpec(image.url, referer=image.referer)

    async def recover_image_request(
        self, image: ImageResource, failed: RequestSpec, response: RequestResponse, context: PluginExecutionContext
    ) -> RequestSpec | None:
        return None

    def auth_flow(self, context: PluginExecutionContext) -> _CsrfFlow:
        return _CsrfFlow(context)

    async def transform_image(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact:
        return artifact
