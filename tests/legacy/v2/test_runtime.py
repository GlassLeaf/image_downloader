from __future__ import annotations

import asyncio
import io
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from image_downloader import BaseDownloader, Chapter, Downloader, ImageResource, ParseResult, PluginRegistry
from image_downloader.cli import build_parser, run
from image_downloader.exceptions import DownloaderError
from image_downloader.observability import ChapterFileSink, ConsoleSink, DownloadLogger
from image_downloader.storage.filesystem import safe_name


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/page":
            body = b"<title>A/Chapter</title><img src='/image'>"
            content_type = "text/html"
        else:
            import io

            from PIL import Image

            stream = io.BytesIO()
            Image.new("RGB", (1, 1), "red").save(stream, format="JPEG")
            body = stream.getvalue()
            content_type = "image/jpeg"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        pass


def test_downloader_saves_image_and_writes_matching_logs(tmp_path: Path) -> None:
    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:

        async def scenario() -> tuple[str, str]:
            output = io.StringIO()
            logger = DownloadLogger([ChapterFileSink(tmp_path / "log.log"), ConsoleSink(output)])
            downloader = Downloader(
                f"http://127.0.0.1:{server.server_port}/page",
                config={"_output_dir": str(tmp_path / "downloads")},
                logger=logger,
            )
            result = await downloader.download()
            await downloader.close()
            assert len(result.saved_files) == 1
            return (
                (tmp_path / "downloads" / "0001_A_Chapter" / "log.log").read_text(encoding="utf-8"),
                output.getvalue(),
            )

        log_file, console = asyncio.run(scenario())
        assert log_file == console
        images = list((tmp_path / "downloads").glob("*/*.jpeg"))
        assert len(images) == 1
        assert images[0].parent.name == "0001_A_Chapter"
        assert images[0].read_bytes().startswith(b"\xff\xd8")
    finally:
        server.shutdown()


def test_safe_name_handles_windows_rules() -> None:
    assert safe_name("a/b") == "a_b"
    assert safe_name("CON") == "_CON"


class FailingImagePlugin(BaseDownloader):
    @classmethod
    def can_handle(cls, url: str) -> bool:
        return url.startswith("failure://")

    async def parse(self, url: str, context: object | None = None) -> ParseResult:
        return ParseResult(
            "failure", [Chapter(1, "failure", images=[ImageResource("https://example.test/missing.png")])]
        )


def test_chapter_log_records_image_error_context(tmp_path: Path) -> None:
    async def scenario() -> str:
        registry = PluginRegistry()
        registry.register(FailingImagePlugin)
        logger = DownloadLogger()
        downloader = Downloader(
            "failure://page", config={"_output_dir": str(tmp_path / "downloads")}, logger=logger, registry=registry
        )

        async def failing_fetch(_spec: object) -> object:
            raise DownloaderError("HTTP request failed: 404")

        downloader.fetch = failing_fetch  # type: ignore[method-assign]
        try:
            try:
                await downloader.download()
            except DownloaderError:
                pass
        finally:
            await downloader.close()
        return (tmp_path / "downloads" / "0001_failure" / "log.log").read_text(encoding="utf-8")

    log = asyncio.run(scenario())
    assert "---\nurl: failure://page/\ntitle: failure\n---" in log
    assert "download: https://example.test/missing.png" in log
    assert "error: image fetch error (1)" in log
    assert "exception: DownloaderError" in log
    assert "detail: HTTP request failed: 404" in log
    assert log.endswith("done\n")


def test_cli_places_chapter_log_beside_output_and_uses_fixed_debug_name(tmp_path: Path, capsys) -> None:
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        config_path = tmp_path / "app.yaml"
        config_path.write_text("", encoding="utf-8")
        args = build_parser().parse_args([f"http://127.0.0.1:{server.server_port}/page", "--config", str(config_path)])
        asyncio.run(run(args))
        profile = tmp_path / "profiles" / "default"
        chapter_logs = list((profile / "downloads").glob("* /log.log".replace(" ", "")))
        assert len(chapter_logs) == 1
        assert chapter_logs[0].parent.glob("*.jpeg")
        debug = profile / "logs" / "debug.log"
        assert debug.exists()
        assert not (profile / "logs" / "custom.log").exists()
        assert "event=response_received module=http" in debug.read_text(encoding="utf-8")
        chapter_log = chapter_logs[0].read_text(encoding="utf-8")
        assert "download: " in chapter_log and "save: " in chapter_log and chapter_log.endswith("done\n")
        assert capsys.readouterr().out == chapter_log
    finally:
        server.shutdown()


def test_cli_update_reports_only_new_urls(tmp_path: Path, capsys) -> None:
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        config_path = tmp_path / "app.yaml"
        config_path.write_text("", encoding="utf-8")
        args = build_parser().parse_args(
            [
                f"http://127.0.0.1:{server.server_port}/page",
                "--config",
                str(config_path),
                "--list-updated-urls",
            ]
        )
        assert asyncio.run(run(args)) == 0
        first = capsys.readouterr()
        assert "updated: 1" in first.err
        assert f"http://127.0.0.1:{server.server_port}/page" in first.out
        assert asyncio.run(run(args)) == 0
        second = capsys.readouterr()
        assert "updated: 0" in second.err
        assert second.out == ""
    finally:
        server.shutdown()
