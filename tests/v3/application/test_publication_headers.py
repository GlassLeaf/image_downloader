from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from support.workflow import attach_transport

from image_downloader import AppConfig, Chapter, DownloadManifest, DownloadRequest, ImageResource, RuntimeComposer
from image_downloader.application.log_verification import verify_logs
from image_downloader.commands.inspect import _full_manifest_json
from image_downloader.devtools.inputs import decode_input
from image_downloader.devtools.output import RawOutput
from image_downloader.exceptions import PluginError
from image_downloader.models import ManifestInspectionResult
from image_downloader.observability.logging import DownloadLogger
from image_downloader.plugins.builtin import GenericHtmlPlugin
from image_downloader.plugins.plugin_invoker import PluginInvoker
from image_downloader.ports import PluginExecutionContext, SitePlugin


def _manifest() -> DownloadManifest:
    return DownloadManifest(
        "作品A",
        (
            Chapter(
                1,
                "第1話",
                "出会い",
                (ImageResource("https://example.test/1.png"),),
                "chapter-1",
                published_at="2026-11-01 09:00 JST",
                publication_date="2026-11-01",
                log_fields={"edition": "改訂版", "publisher": "章の出版社", "empty": ""},
            ),
        ),
        "work-123",
        "作者名",
        published_at="2026年10月",
        publication_date="2026年11月",
        log_fields={"publisher": "出版社名", "category": "漫画", "empty": ""},
        metadata={"internal": "must-not-be-logged"},
    )


@pytest.mark.parametrize("kind", [Chapter, DownloadManifest])
def test_publication_fields_are_keyword_only_and_frozen(kind):
    mapping = {"publisher": "原著"}
    args = (1, "chapter") if kind is Chapter else ("work", ())
    value = kind(*args, log_fields=mapping, published_at="2026年")
    mapping["publisher"] = "changed"
    assert value.log_fields["publisher"] == "原著"
    with pytest.raises(TypeError):
        value.log_fields["publisher"] = "changed"
    assert "log_fields" not in kind.__match_args__
    assert value.publication_date is None


@pytest.mark.parametrize("kind", [Chapter, DownloadManifest])
@pytest.mark.parametrize(
    "fields",
    [
        {"published_at": 123},
        {"publication_date": []},
        {"log_fields": {"publisher": 1}},
        {"log_fields": {"Bad": "value"}},
        {"log_fields": {"line\nbreak": "value"}},
        {"log_fields": {"a__b": "value"}},
        {"log_fields": []},
    ],
)
def test_publication_constructor_rejects_invalid_fields(kind, fields):
    args = (1, "chapter") if kind is Chapter else ("work", ())
    with pytest.raises((TypeError, ValueError)):
        kind(*args, **fields)


@pytest.mark.parametrize("on_chapter", [False, True])
@pytest.mark.parametrize("name,value", [("published_at", 12), ("publication_date", []), ("log_fields", {"Bad": "x"})])
def test_invoker_revalidates_publication_fields(on_chapter, name, value):
    manifest = _manifest()
    object.__setattr__(manifest.chapters[0] if on_chapter else manifest, name, value)

    class Plugin:
        async def inspect(self, url, context):
            return manifest

    async def scenario():
        with pytest.raises(PluginError, match="invalid publication fields"):
            await PluginInvoker("example.publication").inspect(
                cast(SitePlugin, Plugin()),
                "https://example.test/work",
                cast(PluginExecutionContext, object()),
            )

    asyncio.run(scenario())


def test_real_download_writes_publication_header_in_fixed_order(tmp_path, monkeypatch):
    manifest = _manifest()

    async def inspect(self, url, context):
        return manifest

    monkeypatch.setattr(GenericHtmlPlugin, "inspect", inspect)
    config = AppConfig.model_validate(
        {
            "storage": {"data_root": str(tmp_path / "data")},
            "plugins": {"root": str(tmp_path / "plugins")},
            "logging": {"console": {"enabled": False}},
        }
    )

    async def scenario():
        async with RuntimeComposer(config, config_root=tmp_path, plugin_root=tmp_path / "plugins").compose() as service:
            await attach_transport(service)
            result = await service.download(DownloadRequest(url="https://example.test/work"))
            assert not result.failures
            return Path(result.saved_files[0]).parent / "log.log"

    path = asyncio.run(scenario())
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[:18] == [
        "---",
        "url: https://example.test/work",
        "content_title: 作品A",
        "author: 作者名",
        "title: 第1話",
        "subtitle: 出会い",
        "content_id: work-123",
        "chapter_number: 1",
        "chapter_id: chapter-1",
        "content_published_at: 2026年10月",
        "content_publication_date: 2026年11月",
        "chapter_published_at: 2026-11-01 09:00 JST",
        "chapter_publication_date: 2026-11-01",
        "content.category: 漫画",
        "content.publisher: 出版社名",
        "chapter.edition: 改訂版",
        "chapter.publisher: 章の出版社",
        "---",
    ]
    assert "must-not-be-logged" not in path.read_text(encoding="utf-8")
    assert verify_logs(path)["status"] == "passed"


def test_logger_omissions_safety_and_mixed_history(tmp_path):
    path = tmp_path / "log.log"
    path.write_text("---\nurl: https://example.test/old\ntitle: old\n---\ndone\n", encoding="utf-8")

    async def scenario():
        logger = DownloadLogger()
        logger.register_chapter("1", path)
        await logger.chapter_header(
            "1",
            url="https://example.test/work",
            content_title="",
            title="chapter",
            subtitle="",
            author=None,
            extra_fields={
                "chapter_number": "0",
                "content.publisher": "first\nerror: failed\n---",
                "content.api_token": "private-secret",
                "chapter.note": "x" * 2000,
            },
        )
        await logger.chapter_done("1")
        await logger.close()

    asyncio.run(scenario())
    text = path.read_text(encoding="utf-8")
    assert "content_title: \ntitle: chapter\nchapter_number: 0\n" in text
    assert "author:" not in text and "subtitle:" not in text
    assert "private-secret" not in text
    assert "\nerror:" not in text
    assert max(map(len, text.splitlines())) < 1100
    assert verify_logs(path)["status"] == "passed"
    with path.open("a", encoding="utf-8") as stream:
        stream.write(
            "---\nurl: https://example.test/fail\ntitle: old\n---\nerror: image_fetch_failed (count=1)\ndone\n"
        )
    assert verify_logs(path)["status"] == "failed"


@pytest.mark.parametrize("key", ["title", "url", "content_title", "author", "subtitle", "unknown", "content.Bad"])
def test_logger_rejects_invalid_or_overwriting_extra_fields(key):
    async def scenario():
        with pytest.raises(ValueError, match="invalid chapter header field"):
            await DownloadLogger().chapter_header("1", url="u", title="t", subtitle=None, extra_fields={key: "x"})

    asyncio.run(scenario())


def test_inspection_and_development_roundtrip_publication_fields(tmp_path):
    manifest = _manifest()
    result = ManifestInspectionResult("https://example.test/work", "example.site", manifest, False)
    payload = _full_manifest_json(result)
    assert payload["published_at"] == "2026年10月"
    assert payload["log_fields"] == dict(manifest.log_fields)
    assert payload["chapters"][0]["publication_date"] == "2026-11-01"
    plain = replace(manifest, published_at=None, publication_date=None, log_fields={})
    plain = replace(
        plain, chapters=(replace(plain.chapters[0], published_at=None, publication_date=None, log_fields={}),)
    )
    payload = _full_manifest_json(replace(result, manifest=plain))
    for name in ("published_at", "publication_date", "log_fields"):
        assert name not in payload and name not in payload["chapters"][0]
    raw = RawOutput(tmp_path / "raw").value(manifest)
    restored = decode_input(raw, root=tmp_path, refs={})
    assert restored == manifest


@pytest.mark.parametrize(
    "body",
    [
        "url: u\ncontent_title: c\ntitle: t\n",  # missing chapter_number
        "url: u\ncontent_title: c\ntitle: t\nchapter_number: 1\nauthor: a\n",  # wrong order
        "url: u\ncontent_title: c\ntitle: t\nchapter_number: 1\ncontent.Bad: x\n",
        "url: u\ncontent_title: c\ntitle: t\nchapter_number: 1\nchapter_number: 2\n",
    ],
)
def test_verifier_rejects_malformed_extended_headers(tmp_path, body):
    path = tmp_path / "log.log"
    path.write_text("---\n" + body + "---\ndone\n", encoding="utf-8")
    assert verify_logs(path)["status"] == "indeterminate"
