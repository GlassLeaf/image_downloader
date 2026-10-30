from __future__ import annotations

import json
from datetime import UTC, datetime

from image_downloader import (
    ImageResource,
    PluginExecutionContext,
    RequestResponse,
    RequestSpec,
    UpdateCandidate,
    UpdateSnapshot,
)

from .base import BaseGallery, local_origin


class LabPlugin(BaseGallery):
    slug = "short-lived-gallery"

    async def create_image_request(self, image: ImageResource, context: PluginExecutionContext) -> RequestSpec:
        origin = local_origin(context.config["origin"])
        response = await context.requests.execute(RequestSpec(f"{origin}/samples/{self.slug}/issue/{image.image_id}"))
        return RequestSpec(json.loads(response.body)["url"], referer=image.referer)

    async def recover_image_request(
        self,
        image: ImageResource,
        failed: RequestSpec,
        response: RequestResponse,
        context: PluginExecutionContext,
    ) -> RequestSpec | None:
        return await self.create_image_request(image, context) if response.status == 410 else None

    async def check_updates(self, url: str, context: PluginExecutionContext) -> UpdateSnapshot:
        response = await context.requests.execute(RequestSpec(url))
        candidates = tuple(
            UpdateCandidate(item["url"], item["id"], item["revision"]) for item in json.loads(response.body)["works"]
        )
        return UpdateSnapshot(url, candidates, datetime.now(UTC))
