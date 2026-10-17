"""Shared fixtures for additional-file and empty-result acceptance tests."""

from __future__ import annotations

import io

import httpx
from PIL import Image

from image_downloader import AppConfig, RuntimeComposer
from image_downloader.plugins.builtin import GenericHtmlPlugin

URL = "https://example.test/gallery"


def png():
    stream = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(stream, "PNG")
    return stream.getvalue()


async def service(root, handler=None, **overrides):
    config = {
        "storage": {"data_root": str(root / "data")},
        "plugins": {"root": str(root / "plugins")},
        "logging": {"console": {"enabled": False}},
        "output": {"directory_format": "%CHAPTER_NUMBER%", "existing_file": "skip"},
        "network": {"max_attempts": 1},
    }
    config.update(overrides)
    result = RuntimeComposer(AppConfig.model_validate(config), config_root=root, plugin_root=root / "plugins").compose()
    await result.gateway.client.aclose()
    data = png()

    def default(request):
        if request.url.path == "/gallery":
            return httpx.Response(200, text="<img src='/image.png'>", request=request)
        return httpx.Response(200, content=data, headers={"content-type": "image/png"}, request=request)

    result.gateway.client = httpx.AsyncClient(transport=httpx.MockTransport(handler or default))
    return result


def install(monkeypatch, points, provider, received=None, saved=None):
    monkeypatch.setattr(GenericHtmlPlugin, "additional_file_hook_points", lambda self, context: points, raising=False)
    monkeypatch.setattr(GenericHtmlPlugin, "additional_files", provider, raising=False)
    if received:
        monkeypatch.setattr(GenericHtmlPlugin, "additional_file_received", received, raising=False)
    if saved:
        monkeypatch.setattr(GenericHtmlPlugin, "additional_file_saved", saved, raising=False)
