from __future__ import annotations

import asyncio
import io
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from PIL import Image

from image_downloader import (
    BaseDownloader,
    Chapter,
    Downloader,
    ImageResource,
    ParseResult,
    PluginRegistry,
    RequestResponse,
    RequestSpec,
    Token,
    TokenProvider,
)
from image_downloader.auth import AuthProvider, CookieStore
from image_downloader.observability import EventBus


class ProtectedImageHandler(BaseHTTPRequestHandler):
    attempts = 0

    def do_GET(self) -> None:
        type(self).attempts += 1
        if self.headers.get("Authorization") != "Bearer renewed" or self.headers.get("X-CSRF-Token") != "csrf-renewed":
            self.send_response(401)
            self.end_headers()
            return
        stream = io.BytesIO()
        Image.new("RGB", (1, 1), "green").save(stream, format="PNG")
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.end_headers()
        self.wfile.write(stream.getvalue())

    def log_message(self, *_args: object) -> None:
        pass


class RefreshingAuthStrategy:
    def __init__(self) -> None:
        self.calls = 0
        self.token_refreshes = 0
        self.tokens = TokenProvider(self._refresh_token)

    async def _refresh_token(self) -> Token:
        self.token_refreshes += 1
        return Token("csrf-renewed")

    async def refresh_request(self, request: RequestSpec, response: RequestResponse) -> RequestSpec:
        self.calls += 1
        assert response.status == 401
        return replace(
            request,
            headers={**request.headers, "Authorization": "Bearer renewed", "X-CSRF-Token": await self.tokens.get()},
            cookies={"session": "renewed"},
        )


class ProtectedPlugin(BaseDownloader):
    priority = 1000
    strategy: RefreshingAuthStrategy | None = None

    @classmethod
    def can_handle(cls, url: str) -> bool:
        return url.startswith("http://127.0.0.1:")

    async def parse(self, url: str, context: object | None = None) -> ParseResult:
        return ParseResult("protected", [Chapter(1, "protected", images=[ImageResource(url)])])

    def get_auth_strategy(self, context: object = None) -> RefreshingAuthStrategy | None:
        return self.strategy


def test_plugin_refreshes_request_after_401_and_persists_cookie(tmp_path: Path, monkeypatch) -> None:
    import keyring

    values: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(keyring, "get_password", lambda service, account: values.get((service, account)))
    monkeypatch.setattr(
        keyring, "set_password", lambda service, account, value: values.__setitem__((service, account), value)
    )
    server = HTTPServer(("127.0.0.1", 0), ProtectedImageHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    strategy = RefreshingAuthStrategy()
    ProtectedPlugin.strategy = strategy
    registry = PluginRegistry()
    registry.register(ProtectedPlugin)
    cookie_store = CookieStore(tmp_path / "cookie" / "cookies.enc")
    events = EventBus()
    auth_events: list[dict[str, str]] = []
    events.on("on_auth_success", auth_events.append)
    try:

        async def scenario() -> None:
            downloader = Downloader(
                f"http://127.0.0.1:{server.server_port}/image",
                config={"_output_dir": str(tmp_path / "downloads"), "network": {"max_auth_retries": 1}},
                registry=registry,
                events=events,
                auth_provider=AuthProvider(cookie_store=cookie_store),
            )
            try:
                result = await downloader.download()
                assert len(result.saved_files) == 1
            finally:
                await downloader.close()

        asyncio.run(scenario())
        assert strategy.calls == 1
        assert strategy.token_refreshes == 1
        assert ProtectedImageHandler.attempts == 2
        assert {
            (cookie.name, cookie.value) for cookie in cookie_store.load(f"http://127.0.0.1:{server.server_port}/")
        } == {("session", "renewed")}
        assert (tmp_path / "downloads" / "0001_protected" / "0001.png").exists()
        assert auth_events == [{"url": f"http://127.0.0.1:{server.server_port}/image", "status": "401"}]
    finally:
        ProtectedPlugin.strategy = None
        server.shutdown()
