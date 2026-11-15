from __future__ import annotations

from dataclasses import replace

from image_downloader import AuthContext, AuthFlow, AuthLease, BaseAuthFlow, PluginExecutionContext, RequestSpec

from .base import BaseGallery


class TokenFlow(BaseAuthFlow):
    async def initialize(self, auth_context: AuthContext) -> AuthLease:
        self.token = auth_context.secrets.get("api_token")
        return AuthLease()

    def apply(self, request: RequestSpec) -> RequestSpec:
        return replace(request, headers={**request.headers, "Authorization": f"Bearer {self.token}"})


class LabPlugin(BaseGallery):
    slug = "origin-auth-gallery"

    def auth_flow(self, context: PluginExecutionContext) -> AuthFlow:
        return TokenFlow()
