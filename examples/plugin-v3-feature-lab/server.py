"""Loopback-only fictional site. No request headers, cookies or bodies are logged."""

from __future__ import annotations

import io
import json
import secrets
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from PIL import Image

COOKIE_MAX_AGE_SECONDS = 600


class QuietHTTPServer(ThreadingHTTPServer):
    def handle_error(self, request: object, client_address: object) -> None:
        # Do not dump raw exception text from request-handling threads.
        pass


class LabSite:
    def __init__(self) -> None:
        self.cookie_token = secrets.token_urlsafe(32)
        self._cookie_issued_at: float | None = None
        self.lock = threading.Lock()
        self.issued: dict[str, int] = {}
        self.expired: set[str] = set()
        self.transient: set[str] = set()
        self.events: list[tuple[str, str]] = []  # identifiers only, never HTTP data
        self.authorization_seen: list[tuple[str, bool]] = []  # presence only, no credential values
        self.revision = "1"
        self.fail_workflow_once = False
        self.omit_metadata = False
        output = io.BytesIO()
        exif = Image.Exif()
        exif[315] = "Feature lab"
        Image.new("RGB", (6, 4), "blue").save(output, format="PNG", exif=exif)
        self.image = output.getvalue()
        self.servers: list[ThreadingHTTPServer] = []
        self.threads: list[threading.Thread] = []

    def __enter__(self) -> LabSite:
        site = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                pass

            def do_GET(self) -> None:
                site.handle(self)

        for _ in range(2):
            server = QuietHTTPServer(("127.0.0.1", 0), Handler)
            server.daemon_threads = True
            self.servers.append(server)
        self.origin, self.asset_origin = [f"http://127.0.0.1:{server.server_port}" for server in self.servers]
        for server in self.servers:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.threads.append(thread)
        return self

    def __exit__(self, *args: object) -> None:
        for server in self.servers:
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join()

    def reply(self, handler: BaseHTTPRequestHandler, status: int, data: object, **headers: str) -> None:
        body = data if isinstance(data, bytes) else json.dumps(data).encode()
        handler.send_response(status)
        for name, value in {
            "Content-Type": "application/json",
            **{k.replace("_", "-"): v for k, v in headers.items()},
        }.items():
            handler.send_header(name, value)
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def cookie_status(self) -> str:
        """Return only issuance/freshness; never expose the token or request data."""
        with self.lock:
            if self._cookie_issued_at is None:
                return "missing"
            return "ready" if time.monotonic() - self._cookie_issued_at < COOKIE_MAX_AGE_SECONDS else "expired"

    def handle(self, handler: BaseHTTPRequestHandler) -> None:
        parsed = urlsplit(handler.path)
        path = parsed.path
        with self.lock:
            self.authorization_seen.append((path, bool(handler.headers.get("Authorization"))))
        if path == "/cookie-login":
            self.reply(
                handler,
                200,
                b"Local dummy session created. Close this dedicated Chrome window.",
                Content_Type="text/plain",
                Cache_Control="no-store",
                Set_Cookie=(
                    f"lab_session={self.cookie_token}; Path=/; Max-Age={COOKIE_MAX_AGE_SECONDS}; "
                    "HttpOnly; SameSite=Strict"
                ),
            )
            with self.lock:
                self._cookie_issued_at = time.monotonic()
            return
        if path == "/cookie-check":
            jar = SimpleCookie(handler.headers.get("Cookie", ""))
            valid = "lab_session" in jar and secrets.compare_digest(jar["lab_session"].value, self.cookie_token)
            self.reply(handler, 200 if valid else 401, {"ok": valid})
            return
        parts = path.strip("/").split("/")
        if len(parts) < 3 or parts[0] != "samples":
            self.reply(handler, 404, {"error": "unknown_route"})
            return
        slug, action = parts[1:3]
        if slug in {"credential-gallery", "origin-auth-gallery"}:
            if handler.headers.get("Authorization") != "Bearer lab-token":
                self.reply(handler, 401, {"error": "authentication_required"})
                return
        if slug == "cookie-gallery":
            jar = SimpleCookie(handler.headers.get("Cookie", ""))
            if "lab_session" not in jar or jar["lab_session"].value != self.cookie_token:
                self.reply(handler, 401, {"error": "cookie_required"})
                return
        if action == "feed":
            self.reply(
                handler,
                200,
                {"works": [{"url": f"{self.origin}/samples/{slug}/work", "id": "work", "revision": self.revision}]},
            )
        elif action == "work":
            self.reply(handler, 200, self.document(slug))
        elif action == "bootstrap":
            self.reply(handler, 200, {"layout": "standard"})
        elif action == "metadata":
            self.reply(handler, 200, {} if self.omit_metadata else {"variant": "full"})
        elif action == "issue":
            identifier = parts[3]
            with self.lock:
                nonce = self.issued.get(identifier, 0) + 1
                self.issued[identifier] = nonce
                self.events.append(("issue", identifier))
            self.reply(handler, 200, {"url": f"{self.origin}/samples/{slug}/image/{identifier}?ticket={nonce}"})
        elif action == "redirect-denied":
            # Same listener but a different origin spelling; never allow-listed.
            self.reply(handler, 302, {}, Location=f"{self.asset_origin.replace('127.0.0.1', 'localhost')}/sink")
        elif action == "image":
            self.image_response(handler, slug, parts[3], parsed.query)
        else:
            self.reply(handler, 404, {"error": "unknown_route"})

    def document(self, slug: str) -> dict[str, object]:
        chapters = []
        for chapter in range(1, 3):
            images = []
            for index in range(1, 3):
                identifier = f"{chapter}-{index}"
                origin = self.asset_origin if slug == "origin-auth-gallery" else self.origin
                images.append({"id": identifier, "url": f"{origin}/samples/{slug}/image/{identifier}"})
            chapters.append({"id": str(chapter), "title": f"Chapter {chapter}", "images": images})
        return {"id": "work", "title": "Feature lab", "chapters": chapters}

    def image_response(self, handler: BaseHTTPRequestHandler, slug: str, identifier: str, query: str) -> None:
        with self.lock:
            self.events.append(("fetch", identifier))
            if slug == "short-lived-gallery":
                if identifier not in self.expired:
                    self.expired.add(identifier)
                    self.reply(handler, 410, {"error": "ticket_expired"})
                    return
                ticket = parse_qs(query).get("ticket", [""])[0]
                if ticket != str(self.issued.get(identifier)):
                    self.reply(handler, 410, {"error": "ticket_expired"})
                    return
                if self.fail_workflow_once and identifier == "1-2" and identifier not in self.transient:
                    self.transient.add(identifier)
                    self.reply(handler, 503, {"error": "temporary_failure"})
                    return
        self.reply(handler, 200, self.image, Content_Type="image/png")
