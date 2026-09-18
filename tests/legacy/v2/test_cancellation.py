from __future__ import annotations

import asyncio
import io
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from PIL import Image

from image_downloader import BaseDownloader, Chapter, Downloader, ImageResource, ParseResult, PluginRegistry


class SlowImageHandler(BaseHTTPRequestHandler):
    started = threading.Event()

    def do_GET(self) -> None:
        type(self).started.set()
        time.sleep(0.5)
        stream = io.BytesIO()
        Image.new("RGB", (1, 1), "black").save(stream, format="PNG")
        try:
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            self.wfile.write(stream.getvalue())
        except BrokenPipeError:
            pass

    def log_message(self, *_args: object) -> None:
        pass


class SlowPlugin(BaseDownloader):
    priority = 1000

    @classmethod
    def can_handle(cls, url: str) -> bool:
        return url.startswith("http://127.0.0.1:")

    async def parse(self, url: str, context: object | None = None) -> ParseResult:
        return ParseResult("slow", [Chapter(1, "slow", images=[ImageResource(url)])])


def _slow_handler(started: threading.Event) -> type[BaseHTTPRequestHandler]:
    class PerServerSlowHandler(SlowImageHandler):
        def do_GET(self) -> None:
            started.set()
            time.sleep(0.5)
            stream = io.BytesIO()
            Image.new("RGB", (1, 1), "black").save(stream, format="PNG")
            try:
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.end_headers()
                self.wfile.write(stream.getvalue())
            except BrokenPipeError:
                pass

    return PerServerSlowHandler


class MultiHostSlowPlugin(BaseDownloader):
    priority = 1001
    second_url = ""

    @classmethod
    def can_handle(cls, url: str) -> bool:
        return url.startswith("http://127.0.0.1:")

    async def parse(self, url: str, context: object | None = None) -> ParseResult:
        return ParseResult(
            "slow",
            [
                Chapter(1, "first", images=[ImageResource(url)]),
                Chapter(2, "second", images=[ImageResource(self.second_url)]),
            ],
        )


def test_cancellation_releases_downloader_resources(tmp_path: Path) -> None:
    SlowImageHandler.started.clear()
    server = HTTPServer(("127.0.0.1", 0), SlowImageHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    registry = PluginRegistry()
    registry.register(SlowPlugin)
    try:

        async def scenario() -> None:
            downloader = Downloader(
                f"http://127.0.0.1:{server.server_port}/image",
                config={"_output_dir": str(tmp_path / "downloads")},
                registry=registry,
            )
            task = asyncio.create_task(downloader.download())
            assert await asyncio.to_thread(SlowImageHandler.started.wait, 1)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            else:
                raise AssertionError("download task was not cancelled")
            await downloader.close()
            assert downloader.http.client.is_closed

        asyncio.run(scenario())
    finally:
        server.shutdown()


def test_cancellation_releases_multiple_chapters_and_hosts(tmp_path: Path) -> None:
    first_started, second_started = threading.Event(), threading.Event()
    first = HTTPServer(("127.0.0.1", 0), _slow_handler(first_started))
    second = HTTPServer(("127.0.0.1", 0), _slow_handler(second_started))
    threading.Thread(target=first.serve_forever, daemon=True).start()
    threading.Thread(target=second.serve_forever, daemon=True).start()
    MultiHostSlowPlugin.second_url = f"http://127.0.0.1:{second.server_port}/image"
    registry = PluginRegistry()
    registry.register(MultiHostSlowPlugin)
    try:

        async def scenario() -> None:
            downloader = Downloader(
                f"http://127.0.0.1:{first.server_port}/image",
                config={"_output_dir": str(tmp_path / "downloads")},
                registry=registry,
            )
            task = asyncio.create_task(downloader.download())
            assert await asyncio.to_thread(first_started.wait, 1)
            assert await asyncio.to_thread(second_started.wait, 1)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            else:
                raise AssertionError("download task was not cancelled")
            await downloader.close()
            assert downloader.http.client.is_closed

        asyncio.run(scenario())
    finally:
        MultiHostSlowPlugin.second_url = ""
        first.shutdown()
        second.shutdown()
