"""Core-only HTML decoding, precedence, and saved-image integration."""

from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from image_downloader import AppConfig, RuntimeComposer
from image_downloader.models import RequestResponse
from image_downloader.plugins import builtin
from image_downloader.plugins.builtin import GenericHtmlPlugin
from image_downloader.plugins.html_encoding import _decode_html

HTML = '<title>日本語</title><base href="/日本語/"><img src="画像.png" data-image-id="日本語">'


@pytest.mark.parametrize(
    "codec,label,title",
    [
        ("shift_jis", "Shift_JIS", "日本語"),
        ("cp932", "windows-31j", "髙﨑①"),
        ("cp932", "shift-jis", "髙﨑①"),
        ("euc_jp", "EUC-JP", "日本語"),
        ("iso2022_jp", "ISO-2022-JP", "日本語"),
        ("utf-16le", "utf-16le", "日本語"),
        ("utf-16be", "utf-16be", "日本語"),
        ("cp1252", "iso-8859-1", "€"),
    ],
)
def test_declared_encoding_and_web_aliases(codec, label, title):
    html = f"<title>{title}</title>"
    assert _decode_html(html.encode(codec), {"Content-Type": f'text/html; CHARSET=" {label} "'}) == html


@pytest.mark.parametrize("codec", ["utf-8-sig", "utf-16", "utf-16be"])
def test_bom_wins_over_headers_and_meta(codec):
    html = '<meta charset="EUC-JP">' + HTML
    body = html.encode(codec)
    if codec == "utf-16be":
        body = b"\xfe\xff" + body
    assert _decode_html(body, {"content-type": "text/html; charset=windows-1252"}) == html


def test_valid_non_ascii_utf8_wins_over_declarations():
    html = '<meta charset="Shift_JIS">' + HTML
    assert _decode_html(html.encode(), {"content-type": "text/html; charset=euc-jp"}) == html


def test_ascii_iso2022jp_respects_declaration():
    body = HTML.encode("iso2022_jp")
    assert body.isascii()
    assert _decode_html(body, {"content-type": "text/html; charset=iso-2022-jp"}) == HTML


@pytest.mark.parametrize(
    "meta",
    [
        '<meta charset="Shift_JIS">',
        "<META CHARSET=shift_jis>",
        '<meta content="text/html; charset=Shift_JIS" http-equiv=" Content-Type ">',
    ],
)
def test_meta_when_http_missing_or_unknown(meta):
    html = meta + HTML
    for headers in [{}, {"content-type": "text/html; charset=not-real"}]:
        assert _decode_html(html.encode("shift_jis"), headers) == html


def test_http_wins_over_meta_even_when_chosen_decode_needs_replacement():
    html = '<meta charset="Shift_JIS">' + HTML
    body = html.encode("shift_jis")
    assert _decode_html(body, {"content-type": "text/html; charset=utf-8"}) == body.decode("utf-8", "replace")


@pytest.mark.parametrize(
    "prefix",
    [
        '<!-- <meta charset="euc-jp"> -->',
        '<script>"<meta charset=euc-jp>"</script>',
        "<style>/* <meta charset=euc-jp> */</style>",
        "<title><meta charset=euc-jp></title>",
        "<textarea><meta charset=euc-jp></textarea>",
        '<meta charset="unknown">',
        '<meta content="text/html; charset=euc-jp">',
        '<meta http-equiv="Other" content="text/html; charset=euc-jp">',
    ],
)
def test_invalid_or_non_markup_declarations_are_ignored(prefix):
    html = prefix + '<meta charset="Shift_JIS">' + HTML
    assert _decode_html(html.encode("shift_jis"), {}) == html


def test_first_valid_meta_and_first_duplicate_attribute_win():
    html = "<meta charset=unknown><meta charset=shift_jis charset=euc-jp><meta charset=euc-jp>" + HTML
    assert _decode_html(html.encode("shift_jis"), {}) == html


@pytest.mark.parametrize("overflow,used", [(0, True), (1, False), (25, False)])
def test_meta_must_be_complete_inside_first_1024_bytes(overflow, used):
    meta = '<meta charset="shift_jis">'
    padding = 1024 - len(meta) + overflow
    html = " " * padding + meta + HTML
    body = html.encode("shift_jis")
    expected = html if used else body.decode("utf-8", "replace")
    assert _decode_html(body, {}) == expected


@pytest.mark.parametrize("label", [None, "unknown", "utf-7", "iso-2022-kr"])
def test_undeclared_unsupported_and_replacement_encodings_keep_utf8_fallback(label):
    body = HTML.encode("shift_jis")
    headers = {} if label is None else {"content-type": f"text/html; charset={label}"}
    assert _decode_html(body, headers) == body.decode("utf-8", "replace")


def test_special_meta_encodings_and_broken_bom_data():
    body = b'<meta charset="utf-16"><title>\xff</title>'
    assert _decode_html(body, {}) == body.decode("utf-8", "replace")
    body = b'<meta charset="x-user-defined"><title>\x80</title>'
    assert _decode_html(body, {}) == body.decode("cp1252")
    assert _decode_html(b"\xff\xfea", {}) == "\ufffd"
    assert _decode_html(b"", {}) == ""


def test_aozora_declaration_structure_is_decoded_without_network():
    html = (
        '<?xml version="1.0" encoding="Shift_JIS"?>'
        '<meta http-equiv="Content-Type" content="text/html;charset=Shift_JIS" />'
        '<title>夏目漱石 吾輩は猫である</title><img src="a.png">'
    )
    assert _decode_html(html.encode("shift_jis"), {"content-type": "text/html"}) == html


def _context(body, headers):
    async def execute(spec):
        return RequestResponse(spec.url, 200, headers, body)

    return SimpleNamespace(requests=SimpleNamespace(execute=execute))


def test_core_manifest_and_revision_are_independent_of_declared_encoding():
    async def scenario():
        plugin = GenericHtmlPlugin()
        url = "https://example.test/gallery/"
        snapshots = []
        for codec in ["shift_jis", "utf-8"]:
            context = _context(HTML.encode(codec), {"content-type": f"text/html; charset={codec}"})
            manifest = await plugin.inspect(url, context)
            assert manifest.title == manifest.chapters[0].title == "日本語"
            image = manifest.chapters[0].images[0]
            assert image.url == "https://example.test/日本語/画像.png" and image.image_id == "日本語"
            snapshots.append(await plugin.check_updates(url, context))
        assert snapshots[0].candidates == snapshots[1].candidates

    asyncio.run(scenario())


@pytest.mark.parametrize("with_id", [False, True])
def test_revision_change_from_legacy_depends_on_image_fields(monkeypatch, with_id):
    source = '<title>日本語</title><img src="a.png"' + (' data-image-id="日本語"' if with_id else "") + ">"
    context = _context(source.encode("shift_jis"), {"content-type": "text/html; charset=shift_jis"})
    plugin = GenericHtmlPlugin()
    current = asyncio.run(plugin.check_updates("https://example.test/", context))
    monkeypatch.setattr(builtin, "_decode_html", lambda body, headers: body.decode("utf-8", "replace"))
    legacy = asyncio.run(plugin.check_updates("https://example.test/", context))
    assert (current.candidates == legacy.candidates) is (not with_id)


def test_inherited_plugin_retains_legacy_decoding_and_revision(monkeypatch):
    def forbidden(*args):
        pytest.fail("independent subclasses must never use the core decoder")

    monkeypatch.setattr(builtin, "_decode_html", forbidden)

    class IndependentPlugin(GenericHtmlPlugin):
        pass

    async def scenario():
        plugin = IndependentPlugin()
        url = "https://example.test/"
        body = HTML.encode("shift_jis")
        context = _context(body, {"content-type": "text/html; charset=shift_jis"})
        manifest = await plugin.inspect(url, context)
        assert manifest.title == "���{��"
        snapshot = await plugin.check_updates(url, context)
        assert snapshot.candidates[0].revision.startswith("generic-html-image-list-v1:")

    asyncio.run(scenario())


@pytest.mark.parametrize("codec", ["shift_jis", "utf-16"])
def test_core_workflow_saves_unicode_paths_and_original_bytes(tmp_path, monkeypatch, codec):
    client_class = httpx.AsyncClient
    output = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(output, "PNG")
    data = output.getvalue()
    fetched = []

    def respond(request):
        fetched.append(request.url.path)
        if request.url.path == "/gallery/":
            return httpx.Response(
                200, content=HTML.encode(codec), headers={"content-type": f"text/html; charset={codec}"}
            )
        assert request.url.path == "/日本語/画像.png"
        return httpx.Response(200, content=data, headers={"content-type": "image/png"})

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda *a, **kw: client_class(*a, **dict(kw, transport=httpx.MockTransport(respond)))
    )
    config = AppConfig.model_validate(
        {
            "storage": {"data_root": str(tmp_path / "data")},
            "plugins": {"root": str(tmp_path / "plugins")},
            "logging": {"console": {"enabled": False}},
        }
    )

    async def scenario():
        async with RuntimeComposer(config, config_root=tmp_path, plugin_root=tmp_path / "plugins").compose() as service:
            result = await service.workflow("https://example.test/gallery/", workflow_retries=0)
            assert result.items[0].status == "success"
            paths = result.items[0].download.saved_files
            assert len(paths) == 1
            from pathlib import Path

            assert Path(paths[0]).read_bytes() == data
            assert Path(paths[0]).parent.name == "0001_日本語_日本語"

    asyncio.run(scenario())
    assert fetched == ["/gallery/", "/gallery/", "/日本語/画像.png"]
