from __future__ import annotations

from dataclasses import replace

from image_downloader import AuthFlow, PluginExecutionContext, RequestResponse, RequestSpec

from .base import BaseGallery


class TokenFlow:
    def __init__(self, context: PluginExecutionContext) -> None:
        self.token = context.secrets.get("api_token")

    async def apply(self, request: RequestSpec) -> RequestSpec:
        return replace(request, headers={**request.headers, "Authorization": f"Bearer {self.token}"})

    def is_auth_failure(self, request: RequestSpec, response: RequestResponse) -> bool:
        return False

    async def refresh(self, failed: RequestSpec, response: RequestResponse) -> RequestSpec | None:
        return None


class LabPlugin(BaseGallery):
    slug = "credential-gallery"

    def auth_flow(self, context: PluginExecutionContext) -> AuthFlow:
        return TokenFlow(context)
