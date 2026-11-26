"""Persistent image formats apply to downloads and workflows before any I/O."""

from __future__ import annotations

import asyncio

import pytest
from PIL import Image
from support.workflow import FEED, A, Gallery, attach_transport, compose

from image_downloader import DownloadRequest, WorkflowRequest
from image_downloader.diagnostics import diagnostic_for
from image_downloader.exceptions import ConfigurationError


@pytest.mark.parametrize("operation", ["download", "workflow"])
@pytest.mark.parametrize(
    "configured, explicit, quality, expected",
    [
        ("WEBP", None, None, "WEBP"),
        ("JPEG", "PNG", None, "PNG"),
        ("JPEG", "ORIGINAL", None, "PNG"),
        ("ORIGINAL", "JPEG", 80, "JPEG"),
    ],
)
def test_service_uses_configured_format_without_mutating_inputs(
    tmp_path, operation, configured, explicit, quality, expected
):
    async def scenario():
        gallery = Gallery()
        gallery.candidates = (A,)
        async with compose(tmp_path, gallery, image_format=configured) as service:
            await attach_transport(service)
            if operation == "download":
                request = DownloadRequest(url=A.url, image_format=explicit, image_quality=quality)
                download = await service.download(request)
            else:
                request = WorkflowRequest(url=FEED, workflow_retries=0, image_format=explicit, image_quality=quality)
                workflow = await service.workflow(request)
                download = workflow.items[0].download
            assert not download.failures
            path = service.outputs.root / download.saved_files[0]
            assert path.suffix == {"WEBP": ".webp", "JPEG": ".jpeg", "PNG": ".png"}[expected]
            with Image.open(path) as image:
                assert image.format == expected
            assert service.config.output.image_format == configured
            assert request.image_format == explicit and request.image_quality == quality

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["download", "workflow"])
def test_configured_original_quality_conflict_stops_before_plugins_and_network(tmp_path, monkeypatch, operation):
    async def scenario():
        gallery = Gallery()
        async with compose(tmp_path, gallery, image_format="ORIGINAL") as service:
            def unexpected(*args, **kwargs):
                pytest.fail("invalid output settings must stop before plugins, network, events, and retries")

            monkeypatch.setattr(service.registry, "resolve", unexpected)
            monkeypatch.setattr(service.gateway, "execute", unexpected)
            monkeypatch.setattr(service.events, "emit", unexpected)
            if operation == "download":
                request = DownloadRequest(url=A.url, image_quality=80)
            else:
                request = WorkflowRequest(url=FEED, image_quality=80, workflow_retries=2, workflow_retry_delay=0)
            with pytest.raises(ConfigurationError, match="output.image_format") as captured:
                await getattr(service, operation)(request)
            assert diagnostic_for(captured.value).details["field"] == "output.image_format"
            assert not gallery.seen and gallery.checks == 0 and gallery.cleanups == 0
            assert service.config.output.image_format == "ORIGINAL"

    asyncio.run(scenario())
