"""v3 sample: login through a CSRF form using external credential secrets."""

from __future__ import annotations

import re
from collections.abc import Mapping
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
    PluginExecutionContext,
    RequestResponse,
    RequestSpec,
    TransformContext,
)


class _CsrfFlow(BaseAuthFlow):
    async def initialize(self, auth_context: AuthContext) -> AuthLease:
        return AuthLease()

    def classify_response(self, request: RequestSpec, response: RequestResponse) -> AuthDecision:
        if response.status == 200 and b"login-required" in response.body.lower():
            return AuthDecision.AUTH_FAILURE
        return super().classify_response(request, response)

    async def login(self, auth_context: AuthContext) -> AuthLease:
        origin = auth_context.endpoints["operation"].origin
        page = await auth_context.requests.execute(RequestSpec(f"{origin}/login"))
        match = re.search(r"name=['\"]csrf['\"]\s+value=['\"]([^'\"]+)['\"]", page.body.decode("utf-8", "replace"))
        if match is None:
            raise AuthenticationError("login form did not contain a CSRF token")
        session = await auth_context.requests.execute(
            RequestSpec(
                f"{origin}/session",
                method="POST",
                form={
                    "username": auth_context.credentials.get("username"),
                    "password": auth_context.credentials.get("password"),
                    "csrf": match.group(1),
                },
            )
        )
        if session.status >= 400:
            raise AuthenticationError("login session request failed")
        return AuthLease()


class CsrfLoginGalleryPlugin:
    def validate_config(self, config: Mapping[str, object], app_settings: Mapping[str, object]) -> None:
        del app_settings
        if config:
            raise ValueError("CSRF login gallery does not accept configuration")

    def matches(self, url: str) -> bool:
        p = urlparse(url)
        return p.scheme in {"http", "https"} and p.hostname == "csrf-login.example.test"

    async def inspect(self, url: str, context: PluginExecutionContext) -> DownloadManifest:
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
        return _CsrfFlow()

    def transform_image(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact:
        del context
        return artifact
