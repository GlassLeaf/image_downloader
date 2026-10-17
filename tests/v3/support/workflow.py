from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path

import httpx
from PIL import Image

from image_downloader import (
    AppConfig,
    Chapter,
    DownloadManifest,
    ImageResource,
    PluginError,
    RequestSpec,
    RuntimeComposer,
    UpdateCandidate,
    UpdateSnapshot,
)

FEED = "https://example.test/feed"
A = UpdateCandidate("https://example.test/a", "a", "1")
B = UpdateCandidate("https://example.test/b", "b", "1")


def snapshot(*candidates: UpdateCandidate) -> UpdateSnapshot:
    return UpdateSnapshot(FEED, candidates, datetime.now(UTC))


class Gallery:
    candidates = (A, B)
    fail: set[str]

    def __init__(self) -> None:
        self.fail = set()
        self.seen: list[str] = []
        self.checks = 0
        self.cleanups = 0

    def auth_flow(self, context):
        return None

    async def check_updates(self, url, context):
        self.checks += 1
        return UpdateSnapshot(url, self.candidates, datetime.now(UTC))

    async def inspect(self, url, context):
        self.seen.append(url)
        if url in self.fail:
            raise PluginError("private detail")
        return DownloadManifest(url.rsplit("/", 1)[-1], (Chapter(1, "chapter", images=(ImageResource(url + ".png"),)),))

    async def create_image_request(self, image, context):
        return RequestSpec(image.url, auth_required=False)

    async def transform_image(self, artifact, context):
        return artifact

    async def recover_image_request(self, image, failed, response, context):
        return None

    def cleanup_after_use(self):
        self.cleanups += 1


def compose(tmp_path: Path, gallery: Gallery, *, download=None, network=None, workflow_logging=None, **output):
    config = AppConfig.model_validate(
        {
            "storage": {"data_root": str((tmp_path / "data").resolve())},
            "plugins": {"root": str((tmp_path / "plugins").resolve())},
            "logging": {"console": {"enabled": False}},
            "output": output,
            "download": download or {},
            "network": network or {},
            "workflow_logging": workflow_logging or {},
        }
    )
    service = RuntimeComposer(
        config, config_root=tmp_path.resolve(), plugin_root=(tmp_path / "plugins").resolve()
    ).compose()
    record = service.registry.records["core.generic-html"]
    service.registry.resolve = lambda *_args, **_kwargs: (record, gallery)
    return service


async def attach_transport(service, *, bad_url=None):
    data = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(data, "PNG")
    await service.gateway.client.aclose()
    service.gateway.client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                500 if str(request.url) == bad_url else 200,
                content=data.getvalue(),
                headers={"content-type": "image/png"},
            )
        )
    )
