"""A fictional API: credentials stay in AuthFlow, public identity belongs to the core."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from urllib.parse import urlparse

import image_downloader as plugin_api
from image_downloader import (
    AuthContext,
    AuthenticationError,
    AuthFlow,
    AuthLease,
    AuthMode,
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

ORIGIN = "https://auth-snapshot.example.test"


class LoginFlow(BaseAuthFlow):
    async def refresh(self, auth_context: AuthContext) -> AuthLease:
        return await self.login(auth_context)

    async def login(self, auth_context: AuthContext) -> AuthLease:
        response = await auth_context.requests.execute(
            RequestSpec(
                f"{ORIGIN}/api/login",
                method="POST",
                auth=AuthMode.NONE,
                json={
                    "username": auth_context.credentials.get("username"),
                    "password": auth_context.credentials.get("password"),
                },
            )
        )
        if response.status != 200:
            raise AuthenticationError("mock login failed")
        payload = json.loads(response.body)
        self._token = payload["access_token"]
        return AuthLease(payload["expires_in"], public_data={"user_id": payload["user_id"]})

    def apply(self, request: RequestSpec) -> RequestSpec:
        return replace(request, headers={**request.headers, "Authorization": f"Bearer {self._token}"})


class AuthSnapshotGallery:
    def validate_config(self, config: Mapping[str, object], app_settings: Mapping[str, object]) -> None:
        if "auth_snapshot" not in getattr(plugin_api, "PLUGIN_API_FEATURES", ()):
            raise ValueError("This plugin requires an image-downloader runtime with auth_snapshot support")

    def matches(self, url: str) -> bool:
        parsed = urlparse(url)
        return parsed.scheme == "https" and parsed.hostname == "auth-snapshot.example.test"

    def auth_flow(self, context: PluginExecutionContext) -> AuthFlow | None:
        return LoginFlow()

    async def inspect(self, url: str, context: PluginExecutionContext) -> DownloadManifest:
        await context.auth.ensure_ready()
        snapshot = context.auth.snapshot()  # Synchronous; no verification or HTTP here.
        response = await context.requests.execute(
            RequestSpec(f"{ORIGIN}/api/gallery", query={"user_id": str(snapshot.public_data["user_id"])})
        )
        if response.status != 200:
            raise ValueError("mock gallery request failed")
        payload = json.loads(response.body)
        images = tuple(ImageResource(f"{ORIGIN}{path}", index=index) for index, path in enumerate(payload["images"], 1))
        return DownloadManifest(payload["title"], (Chapter(1, payload["title"], images=images),))

    async def create_image_request(self, image: ImageResource, context: PluginExecutionContext) -> RequestSpec:
        return RequestSpec(image.url)

    async def recover_image_request(
        self, image: ImageResource, failed: RequestSpec, response: RequestResponse, context: PluginExecutionContext
    ) -> RequestSpec | None:
        return None

    def transform_image(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact:
        return artifact
