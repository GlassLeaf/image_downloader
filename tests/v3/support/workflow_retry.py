from __future__ import annotations

import io

import httpx
from PIL import Image

from image_downloader import (
    Chapter,
    DownloadManifest,
    ImageResource,
    RequestSpec,
)
from support.workflow import A, Gallery


class RetryGallery(Gallery):
    candidates = (A,)

    def __init__(self):
        super().__init__()
        self.requested = []
        self.manifests = {}
        self.failure = None

    async def inspect(self, url, context):
        self.seen.append(url)
        if self.failure is not None and self.checks == 1:
            raise self.failure
        return self.manifests.get((self.checks, url), self.manifests.get((1, url), manifest()))

    async def create_image_request(self, image, context):
        self.requested.append((self.checks, image.url))
        return RequestSpec(image.url, auth_required=False)


def manifest(*images):
    if not images:
        images = (
            ImageResource(A.url + "/good.png", image_id="good", index=1),
            ImageResource(A.url + "/bad.png", image_id="bad", index=2),
        )
    return DownloadManifest("gallery", (Chapter(1, "chapter", images=images, chapter_id="chapter"),))


async def transport(service, gallery, *, permanent=False):
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(buffer, "PNG")
    await service.gateway.client.aclose()

    def respond(request):
        failed = str(request.url).endswith("/bad.png") and (gallery.checks == 1 or permanent)
        return httpx.Response(503 if failed else 200, content=buffer.getvalue(), headers={"content-type": "image/png"})

    service.gateway.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
