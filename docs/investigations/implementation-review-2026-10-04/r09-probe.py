"""Reproduce R09 without changing application code or contacting sites by default."""

from __future__ import annotations

import asyncio
import io
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import httpx
from PIL import Image

from image_downloader import AppConfig, RuntimeComposer
from image_downloader.models import RequestResponse
from image_downloader.plugins.builtin import GenericHtmlPlugin, _ImageParser


async def inspect_bytes(body, content_type, url="https://example.test/gallery/"):
    async def execute(spec):
        return RequestResponse(url, 200, {"content-type": content_type}, body)

    context = SimpleNamespace(requests=SimpleNamespace(execute=execute))
    return await GenericHtmlPlugin().inspect(url, context)


async def fixtures():
    html = '<title>日本語</title><img src="日本語.png" data-image-id="日本語">'
    cases = [
        ("utf8", html.encode("utf-8"), "text/html; charset=utf-8", "utf-8"),
        ("shift_jis_header", html.encode("shift_jis"), "text/html; charset=shift_jis", "shift_jis"),
        ("shift_jis_meta", ('<meta charset="Shift_JIS">' + html).encode("shift_jis"), "text/html", "shift_jis"),
        (
            "euc_jp_meta",
            ('<meta http-equiv="Content-Type" content="text/html; charset=EUC-JP">' + html).encode("euc_jp"),
            "text/html",
            "euc_jp",
        ),
        (
            "cp932_extensions",
            '<title>髙﨑①</title><img src="a.png">'.encode("cp932"),
            "text/html; charset=windows-31j",
            "cp932",
        ),
        ("iso2022jp", html.encode("iso2022_jp"), "text/html; charset=iso-2022-jp", "iso2022_jp"),
        ("utf16_bom", html.encode("utf-16"), "text/html; charset=utf-16", "utf-16"),
        ("utf8_bom", html.encode("utf-8-sig"), "text/html", "utf-8-sig"),
        (
            "ascii_references",
            '<title>&#26085;&#26412;&#35486;</title><img src="a.png">'.encode("shift_jis"),
            "text/html; charset=shift_jis",
            "shift_jis",
        ),
    ]
    for name, body, content_type, codec in cases:
        actual = await inspect_bytes(body, content_type)
        expected = _ImageParser("https://example.test/gallery/")
        expected.feed(body.decode(codec))
        expected.close()
        http_urls = []
        for image in actual.chapters[0].images:
            try:
                http_urls.append(str(httpx.URL(image.url)))
            except httpx.InvalidURL as exc:
                http_urls.append(f"InvalidURL: {exc}")
        print(
            json.dumps(
                {
                    "case": name,
                    "expected_title": expected.title,
                    "actual_title": actual.title,
                    "images": [image.url for image in actual.chapters[0].images],
                    "image_ids": [image.image_id for image in actual.chapters[0].images],
                    "http_urls": http_urls,
                },
                ensure_ascii=True,
            )
        )
    print("shift_jis_title_hex", "日本語".encode("shift_jis").hex(" "))
    for with_id in [False, True]:
        snapshots = []
        for codec in ["shift_jis", "utf-8"]:
            source = '<title>日本語</title><img src="a.png"' + (' data-image-id="日本語"' if with_id else "") + ">"

            async def execute(spec, codec=codec, source=source):
                return RequestResponse(
                    spec.url, 200, {"content-type": "text/html; charset=" + codec}, source.encode(codec)
                )

            context = SimpleNamespace(requests=SimpleNamespace(execute=execute))
            snapshots.append(await GenericHtmlPlugin().check_updates("https://example.test/gallery/", context))
        print(
            json.dumps(
                {
                    "revision_with_japanese_id": with_id,
                    "revision_equal": snapshots[0].candidates[0].revision == snapshots[1].candidates[0].revision,
                }
            )
        )


async def workflow_cases():
    output = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(output, format="PNG")
    for name, html, codec, mime in [
        ("title_only", '<title>日本語</title><img src="a.png">', "shift_jis", "text/html; charset=shift_jis"),
        (
            "unicode_image_url",
            '<title>日本語</title><img src="日本語.png">',
            "shift_jis",
            "text/html; charset=shift_jis",
        ),
        ("utf16_empty_extraction", '<title>日本語</title><img src="a.png">', "utf-16", "text/html; charset=utf-16"),
    ]:
        with tempfile.TemporaryDirectory(prefix="r09-") as directory:
            root = Path(directory)
            config = AppConfig.model_validate(
                {
                    "storage": {"data_root": str(root / "data")},
                    "plugins": {"root": str(root / "plugins")},
                    "network": {"max_attempts": 1},
                    "logging": {"console": {"enabled": False}},
                }
            )
            fetched = []

            def respond(request, fetched=fetched, html=html, codec=codec, mime=mime):
                fetched.append(str(request.url))
                if request.url.path == "/gallery/":
                    return httpx.Response(200, content=html.encode(codec), headers={"content-type": mime})
                if request.url.path in {"/gallery/a.png", "/gallery/日本語.png"}:
                    return httpx.Response(200, content=output.getvalue(), headers={"content-type": "image/png"})
                return httpx.Response(404)

            async with RuntimeComposer(config, config_root=root, plugin_root=root / "plugins").compose() as service:
                await service.gateway.client.aclose()
                service.gateway.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
                result = await service.workflow("https://example.test/gallery/", workflow_retries=0)
                item = result.items[0]
                download = item.download
                print(
                    json.dumps(
                        {
                            "workflow_case": name,
                            "status": item.status,
                            "requests": fetched,
                            "saved": [str(Path(path).relative_to(root)) for path in download.saved_files],
                            "failures": [failure.code for failure in download.failures],
                        },
                        ensure_ascii=True,
                    )
                )


async def live_example():
    url = "https://www.aozora.gr.jp/cards/000148/files/789_14547.html"
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        response = await client.get(url)
        response.raise_for_status()
    body = response.content
    actual = await inspect_bytes(body, response.headers.get("content-type", ""), str(response.url))
    expected = _ImageParser(str(response.url))
    expected.feed(body.decode("shift_jis"))
    expected.close()
    print(
        json.dumps(
            {
                "live_url": str(response.url),
                "status": response.status_code,
                "content_type": response.headers.get("content-type"),
                "head_source": body[:400].decode("ascii", errors="replace"),
                "expected_title": expected.title,
                "actual_title": actual.title,
                "image_count": len(actual.chapters[0].images),
                "image_urls_equal": [image.url for image in actual.chapters[0].images]
                == [image.url for image in expected.images],
            },
            ensure_ascii=True,
        )
    )


async def main():
    if "--legacy" in sys.argv:
        # Recreate the pre-R09 core policy without modifying repository sources.
        from image_downloader.plugins import builtin

        builtin._decode_html = lambda body, headers: body.decode("utf-8", errors="replace")
    if "--live" in sys.argv:
        await live_example()
    else:
        await fixtures()
        await workflow_cases()


if __name__ == "__main__":
    asyncio.run(main())
