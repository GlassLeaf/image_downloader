"""Offline audit probes: assert the observed current behavior, not desired fixes.

Run from the repository root:
python -m pytest docs/investigations/implementation-review-2026-10-05/behavior-probe.py -q -s
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests/v3/application"))

from test_generic_workflow import compose  # noqa: E402
from test_workflow_verification import make_run  # noqa: E402

from image_downloader import ImageSaveOptions, WorkflowStateService  # noqa: E402
from image_downloader.application.workflow_verification import verify_workflow  # noqa: E402
from image_downloader.configuration.layers import _sanitize_user_layer  # noqa: E402
from image_downloader.media.image_processor import _process_image  # noqa: E402
from image_downloader.plugins.builtin import GenericHtmlPlugin  # noqa: E402


def result(name, **values):
    print(json.dumps({"probe": name, **values}, ensure_ascii=False))


def png(color):
    stream = io.BytesIO()
    Image.new("RGB", (2, 2), color).save(stream, "PNG")
    return stream.getvalue()


def test_collision_empty_and_direct_image(tmp_path, monkeypatch):
    original_client = httpx.AsyncClient
    first_bytes, second_bytes = png("red"), png("blue")

    def respond(request):
        path = request.url.path
        if path in {"/one", "/two"}:
            image = "red" if path == "/one" else "blue"
            return httpx.Response(200, text=f'<title>Same</title><img src="/{image}.png">')
        if path == "/empty":
            return httpx.Response(200, text="<title>Login required</title><p>Sign in</p>")
        return httpx.Response(
            200,
            content=first_bytes if path == "/red.png" else second_bytes,
            headers={"content-type": "image/png"},
        )

    def client(*args, **kwargs):
        return original_client(*args, **dict(kwargs, transport=httpx.MockTransport(respond)))

    monkeypatch.setattr(httpx, "AsyncClient", client)

    async def scenario():
        async with compose(tmp_path) as service:
            first = await service.run("https://example.test/one")
            saved = Path(first.saved_files[0])
            assert saved.read_bytes() == first_bytes
            second = await service.run("https://example.test/two")
            assert first.saved_files == second.saved_files
            assert saved.read_bytes() == second_bytes
            result("R05", identical_path=True, previous_bytes_overwritten=True)
            empty = await service.workflow("https://example.test/empty", workflow_retries=0)
            assert empty.items[0].status == "success"
            assert not empty.items[0].download.saved_files
            assert all(
                item.completed
                for item in WorkflowStateService(service.config).get_workflow("https://example.test/empty")[0].items
            )
            repeat = await service.workflow("https://example.test/empty", workflow_retries=0)
            assert not repeat.selected_urls
            result("R03", zero_images_status="success", completed=True, next_selected=0)
            direct = await service.run("https://example.test/red.png")
            assert not direct.saved_files and not direct.failures
            result("R10", image_response_saved=0, failures=0)
            initial = await service.workflow("https://example.test/one", workflow_retries=0)
            Path(initial.items[0].download.saved_files[0]).unlink()
            repeat = await service.workflow("https://example.test/one", workflow_retries=0)
            assert not repeat.selected_urls
            result("R20", local_file_missing=True, updated_selected=0)

    asyncio.run(scenario())


def test_responsive_markup():
    html = '<picture><source srcset="large.png 2x"><img data-src="actual.png"></picture>'

    async def execute(spec):
        return SimpleNamespace(url=spec.url, body=html.encode(), headers={"content-type": "text/html"})

    context = SimpleNamespace(requests=SimpleNamespace(execute=execute))
    manifest = asyncio.run(GenericHtmlPlugin().inspect("https://example.test/", context))
    assert not manifest.chapters[0].images
    result("R08", responsive_or_lazy_images=0)


def test_animation_conversion():
    stream = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(
        stream,
        "GIF",
        save_all=True,
        append_images=[Image.new("RGB", (2, 2), "blue")],
        duration=100,
        loop=0,
    )
    original = stream.getvalue()
    assert Image.open(io.BytesIO(original)).n_frames == 2
    output, _ = _process_image(
        original, "https://example.test/a.gif", "image/gif", ImageSaveOptions(format="WEBP"), None
    )
    frames = Image.open(io.BytesIO(output)).n_frames
    assert frames == 1
    result("R12", input_frames=2, converted_frames=frames)


def test_presence_verification_accepts_nonimage(tmp_path):
    verification = verify_workflow(make_run(tmp_path), tmp_path)
    assert verification["status"] == "passed"
    result("R17", nonimage_nonempty_file_verification=verification["status"])


def test_removed_setting_is_not_migrated():
    sanitized, removals = _sanitize_user_layer({"network": {"max_retries": 9, "requset_timeout_seconds": 90}})
    assert sanitized == {"network": {}}
    assert len(removals) == 2
    result("R06", sanitized=sanitized, removed=[".".join(path) for path in removals], migrated=False)
