"""Run independent plugins through real selection, library, and CLI paths."""

from __future__ import annotations

import asyncio
import io
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from PIL import Image

from image_downloader import AppConfig, RuntimeComposer
from image_downloader.cli import main
from image_downloader.immutable import freeze_json
from image_downloader.models import (
    Chapter,
    DownloadManifest,
    ImageResource,
    RequestSpec,
    UpdateCandidate,
    UpdateSnapshot,
)
from image_downloader.plugins import builtin, html_encoding
from image_downloader.plugins.builtin import GenericHtmlPlugin, _ImageParser
from image_downloader.plugins.lifecycle import PluginRecord, PluginRegistry
from image_downloader.plugins.runtime import _builtin_manifest

PLUGIN_ID = "example.independent"
URL = "https://example.test/gallery/"
SOURCE = '<title>日本語</title><img src="a.png" data-image-id="日本語">'
BODY = SOURCE.encode("shift_jis")
HEADERS = {"content-type": "text/html; charset=shift_jis", "x-custom": "preserved"}
MANIFEST = DownloadManifest(
    "日本語", (Chapter(1, "日本語", images=(ImageResource(URL + "a.png", 1, referer=URL, image_id="日本語"),)),)
)


@pytest.fixture
def independent_runtime(tmp_path, monkeypatch):
    calls = []
    fetched = []

    def forbidden(*args, **kwargs):
        pytest.fail("another plugin or command called the core HTML decoder")

    monkeypatch.setattr(builtin, "_decode_html", forbidden)
    monkeypatch.setattr(html_encoding, "_decode_html", forbidden)

    class IndependentPlugin(GenericHtmlPlugin):
        def matches(self, url):
            return url.startswith(URL)

        async def inspect(self, url, context):
            calls.append("inspect")
            response = await context.requests.execute(RequestSpec(url))
            assert response.body == BODY
            assert all(response.headers[name] == value for name, value in HEADERS.items())
            # Deliberately owned by this independent plugin, not the core decoder.
            parser = _ImageParser(response.url)
            parser.feed(response.body.decode("shift_jis"))
            parser.close()
            manifest = DownloadManifest(parser.title, (Chapter(1, parser.title, images=tuple(parser.images)),))
            assert manifest == MANIFEST
            return manifest

        async def check_updates(self, url, context):
            calls.append("check")
            return UpdateSnapshot(url, (UpdateCandidate(url, revision="independent-v1"),), datetime.now(UTC))

    compose_registry = RuntimeComposer.compose_registry

    def registry_for(composer):
        registry = compose_registry(composer)
        manifest = _builtin_manifest()
        manifest = replace(manifest, value=freeze_json({**manifest.value, "id": PLUGIN_ID}))
        record = PluginRecord(manifest, {}, None)
        registry.loader.register_class(record, IndependentPlugin)
        registry.registry = PluginRegistry({**registry.records, PLUGIN_ID: record}, catalog=registry.catalog)
        return registry

    monkeypatch.setattr(RuntimeComposer, "compose_registry", registry_for)
    output = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(output, "PNG")
    data = output.getvalue()
    client_class = httpx.AsyncClient

    def respond(request):
        fetched.append(str(request.url))
        if request.url.path == "/gallery/":
            return httpx.Response(200, content=BODY, headers=HEADERS)
        assert request.url.path == "/gallery/a.png"
        return httpx.Response(200, content=data, headers={"content-type": "image/png"})

    def client(*args, **kwargs):
        return client_class(*args, **dict(kwargs, transport=httpx.MockTransport(respond)))

    monkeypatch.setattr(httpx, "AsyncClient", client)
    config_path = tmp_path / "app.yaml"
    config_path.write_text(
        f"storage:\n  data_root: '{tmp_path / 'data'}'\nplugins:\n  root: '{tmp_path / 'plugins'}'\n"
        "logging:\n  console:\n    enabled: false\n",
        encoding="utf-8",
    )
    config = AppConfig.model_validate(
        {
            "storage": {"data_root": str(tmp_path / "data")},
            "plugins": {"root": str(tmp_path / "plugins")},
            "logging": {"console": {"enabled": False}},
        }
    )
    return config, config_path, data, calls, fetched


@pytest.mark.parametrize("selection", ["automatic", "explicit", "forced"])
def test_independent_plugin_library_operations_do_not_call_core_decoder(independent_runtime, selection):
    config, config_path, data, calls, fetched = independent_runtime
    options = {} if selection == "automatic" else {"plugin_id": PLUGIN_ID, "force_plugin": selection == "forced"}

    async def scenario():
        async with RuntimeComposer(
            config, config_root=config_path.parent, plugin_root=config_path.parent / "plugins"
        ).compose() as service:
            inspection = await service.inspect(URL, **options)
            assert inspection.plugin_id == PLUGIN_ID
            assert inspection.manifest == replace(MANIFEST, metadata={"source_url": URL})
            update = await service.check_updates(URL, **options)
            assert update.plugin_id == PLUGIN_ID and update.changes[0].revision == "independent-v1"
            plan = await service.plan_workflow(URL, **options)
            assert plan.selected_urls == (URL,)
            download = await service.run(URL, **options)
            assert not download.failures and len(download.saved_files) == 1
            assert Path(download.saved_files[0]).read_bytes() == data
            assert Path(download.saved_files[0]).parent.name == "0001_日本語_日本語"
            workflow = await service.workflow(URL, workflow_retries=0, download_scope="all", **options)
            assert workflow.items[0].status == "success"
            assert workflow.snapshot.candidates[0].revision == "independent-v1"
            assert Path(workflow.items[0].download.saved_files[0]).read_bytes() == data
            before = len(calls)
            unchanged = await service.workflow(URL, workflow_retries=0, **options)
            assert unchanged.selected_urls == ()
            assert calls[before:] == ["check"]

    asyncio.run(scenario())
    assert all(url in {URL, URL + "a.png"} for url in fetched)


@pytest.mark.parametrize("selection", ["automatic", "explicit", "forced"])
@pytest.mark.parametrize(
    "command", ["download", "bare", "inspect", "inspect_alias", "workflow_all", "workflow_updated", "workflow_plan"]
)
def test_independent_plugin_cli_operations_do_not_call_core_decoder(independent_runtime, selection, command, capsys):
    config, config_path, data, calls, fetched = independent_runtime
    forms = {
        "download": ["download", URL],
        "bare": [URL],
        "inspect": ["inspect", URL],
        "inspect_alias": ["download", URL, "--inspect-only"],
        "workflow_all": ["workflow", URL, "--download-scope", "all"],
        "workflow_updated": ["workflow", URL, "--download-scope", "updated"],
        "workflow_plan": ["workflow", URL, "--dry-run"],
    }
    selection_args = (
        [] if selection == "automatic" else ["--force-plugin" if selection == "forced" else "--plugin", PLUGIN_ID]
    )
    assert main([*forms[command], *selection_args, "--config", str(config_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload
    if command in {"inspect", "inspect_alias"}:
        assert calls == ["inspect"] and fetched == [URL]
    elif command == "workflow_plan":
        assert calls == ["check"] and not fetched
    else:
        assert "inspect" in calls and URL + "a.png" in fetched
        saved = list((config_path.parent / "data").rglob("0001.png"))
        assert len(saved) == 1 and saved[0].read_bytes() == data
        assert saved[0].parent.name == "0001_日本語_日本語"


def test_core_fallback_uses_decoder_only_when_no_independent_plugin_matches(independent_runtime, monkeypatch):
    config, config_path, _, _, _ = independent_runtime
    # The fixture forbids decoding; switch the builtin binding to a counting wrapper.
    calls = []

    def observe(body, headers):
        calls.append((body, headers))
        return "<title>fallback</title>"

    monkeypatch.setattr(builtin, "_decode_html", observe)

    async def scenario():
        async with RuntimeComposer(
            config, config_root=config_path.parent, plugin_root=config_path.parent / "plugins"
        ).compose() as service:
            await service.inspect(URL)
            assert not calls
            # This URL does not match the custom plugin. Mock response bytes are immaterial to this assertion.
            result = await service.inspect("https://other.test/gallery/")
            assert result.plugin_id == "core.generic-html" and result.manifest.title == "fallback"
            assert len(calls) == 1

    asyncio.run(scenario())
