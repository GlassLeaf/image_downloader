from __future__ import annotations

import asyncio
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from examples.auth.form_csrf_login import FormCsrfLoginExampleStrategy
from PIL import Image

from image_downloader import (
    AuthenticationError,
    BaseDownloader,
    Chapter,
    Credentials,
    Downloader,
    ImageResource,
    ParseResult,
    PluginRegistry,
    RequestResponse,
)


class FormLoginHandler(BaseHTTPRequestHandler):
    requests: list[str] = []

    def do_GET(self) -> None:
        type(self).requests.append(self.path)
        if self.path == "/login":
            body, content_type = b"<input value='csrf-value' type='hidden' name='csrf'>", "text/html"
        elif self.path == "/api/auth/session":
            logged_in = "session=valid" in self.headers.get("Cookie", "")
            body = json.dumps(
                {"success": logged_in, **({} if logged_in else {"code": "expired", "message": "session expired"})}
            ).encode()
            content_type = "application/json"
        elif self.path == "/protected" and "session=valid" in self.headers.get("Cookie", ""):
            stream = io.BytesIO()
            Image.new("RGB", (1, 1), "purple").save(stream, format="PNG")
            body, content_type = stream.getvalue(), "image/png"
        elif self.path == "/protected":
            self.send_response(401)
            self.end_headers()
            return
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        type(self).requests.append(self.path)
        size = int(self.headers.get("Content-Length", "0"))
        form = parse_qs(self.rfile.read(size).decode())
        if self.path == "/api/auth/login" and form == {
            "username": ["user"],
            "password": ["password"],
            "csrf": ["csrf-value"],
        }:
            self.send_response(200)
            self.send_header("Set-Cookie", "session=valid; Path=/; HttpOnly")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"success": false, "code": "bad_login", "message": "invalid credentials"}')

    def log_message(self, *_args: object) -> None:
        pass


class FormLoginPlugin(BaseDownloader):
    priority = 1002

    @classmethod
    def can_handle(cls, url: str) -> bool:
        return url.startswith("http://127.0.0.1:")

    async def parse(self, url: str, context: object | None = None) -> ParseResult:
        return ParseResult("protected", [Chapter(1, "protected", images=[ImageResource(url)])])

    def get_auth_strategy(self, context: object = None) -> FormCsrfLoginExampleStrategy:
        assert context is not None and hasattr(context, "fetch")
        origin = self._origin(context.url)
        return FormCsrfLoginExampleStrategy(
            fetch=context.fetch,
            credentials=Credentials("user", "password"),
            login_page_url=f"{origin}/login",
            login_api_url=f"{origin}/api/auth/login",
            session_url=f"{origin}/api/auth/session",
        )

    @staticmethod
    def _origin(url: str) -> str:
        return url.rsplit("/", 1)[0]


def test_form_login_csrf_session_validation_and_cookie_retry(tmp_path: Path) -> None:
    FormLoginHandler.requests = []
    server = HTTPServer(("127.0.0.1", 0), FormLoginHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    registry = PluginRegistry()
    registry.register(FormLoginPlugin)
    try:

        async def scenario() -> None:
            downloader = Downloader(
                f"http://127.0.0.1:{server.server_port}/protected",
                config={"_output_dir": str(tmp_path / "downloads")},
                registry=registry,
            )
            try:
                result = await downloader.download()
                assert len(result.saved_files) == 1
            finally:
                await downloader.close()

        asyncio.run(scenario())
        assert FormLoginHandler.requests == [
            "/protected",
            "/login",
            "/api/auth/login",
            "/api/auth/session",
            "/protected",
        ]
        assert (tmp_path / "downloads" / "0001_protected" / "0001.png").exists()
    finally:
        server.shutdown()


def test_form_login_reports_session_failure_code_and_message() -> None:
    response = RequestResponse(
        "http://example.test/api/auth/session",
        200,
        {},
        b'{"success": false, "code": "expired", "message": "session expired"}',
    )
    try:
        FormCsrfLoginExampleStrategy.verify_session(response)
    except AuthenticationError as exc:
        assert str(exc) == "login validation failed: expired: session expired"
    else:
        raise AssertionError("invalid session was accepted")


def test_form_login_strategy_is_not_a_public_library_api() -> None:
    import image_downloader
    import image_downloader.auth

    assert not hasattr(image_downloader, "FormLoginAuthStrategy")
    assert not hasattr(image_downloader.auth, "FormLoginAuthStrategy")
