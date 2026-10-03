"""Real SMTP and subprocess CLI tests; enable IMAGE_DOWNLOADER_TEST_MAILPIT=1."""

from __future__ import annotations

import asyncio
import io
import json
import os
import re
import smtplib
import socketserver
import subprocess
import sys
import threading
import time
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from PIL import Image

from image_downloader.config import Notification
from image_downloader.observability.events import EventBus, EventName, EventPayload
from image_downloader.observability.logging import DebugFileSink, DownloadLogger
from image_downloader.observability.notifications import EmailNotificationSender, NotificationService

ROOT = Path(__file__).resolve().parents[3]
API = "http://localhost:8025"
FROM = "image-downloader@test.example.test"
pytestmark = [
    pytest.mark.mailpit,
    pytest.mark.skipif(os.getenv("IMAGE_DOWNLOADER_TEST_MAILPIT") != "1", reason="local Mailpit opt-in required"),
]


class MailCase:
    def __init__(self, name, root, report_dir):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^a-zA-Z0-9-]", "-", name)
        self.recipient = f"{safe_name[:25]}-{uuid4().hex}@test.example.test"
        self.report_path = report_dir / f"{name}-{uuid4().hex}.json"
        self.report = {"case": name, "recipient": self.recipient, "messages": [], "cli": [], "checks": []}
        self.client = httpx.Client(base_url=API, timeout=5, trust_env=False)

    def check(self, description):
        self.report["checks"].append(description)

    def record_outcome(self, result):
        self.report[result.when] = result.outcome
        if result.failed:
            self.report["failure"] = str(result.longrepr)
        self.report_path.write_text(json.dumps(self.report, ensure_ascii=False, indent=2), encoding="utf-8")

    def notification(self, **overrides):
        values = {
            "enabled": True,
            "methods": ["email"],
            "notify_on": ["fetch_error", "process_error", "save_error", "download_success", "download_partial_success"],
            "email": {
                "smtp_host": "localhost",
                "smtp_port": 1025,
                "use_tls": False,
                "username": "",
                "from": FROM,
                "to": [self.recipient],
            },
        }
        values.update(overrides)
        return Notification.model_validate(values)

    def messages(self):
        response = self.client.get("/api/v1/search", params={"query": f"to:{self.recipient}", "limit": 100})
        response.raise_for_status()
        return response.json()["messages"]

    def expect_mail(self, count):
        deadline = time.monotonic() + (10 if count else 0)
        messages = self.messages()
        while len(messages) < count and time.monotonic() < deadline:
            time.sleep(0.2)
            messages = self.messages()
        # Observe the entire quiet period, including negative expectations.
        quiet_until = time.monotonic() + 2
        while time.monotonic() < quiet_until:
            assert len(messages) == count, f"expected {count} emails, got {len(messages)}"
            time.sleep(0.2)
            messages = self.messages()
        assert len(messages) == count
        decoded = []
        for summary in messages:
            message_id = summary["ID"]
            detail = self.client.get(f"/api/v1/message/{message_id}")
            detail.raise_for_status()
            raw = self.client.get(f"/api/v1/message/{message_id}/raw")
            raw.raise_for_status()
            message = BytesParser(policy=policy.default).parsebytes(raw.content)
            assert message["From"] == FROM
            assert message["To"] == self.recipient
            body = message.get_body(preferencelist=("plain",)).get_content().replace("\r\n", "\n")
            assert detail.json()["Text"].replace("\r\n", "\n").strip() == body.strip()
            decoded.append((message, body))
            self.report["messages"].append({"id": message_id, "subject": str(message["Subject"]), "body": body})
        self.check(f"received {count} emails; no duplicates during 2-second observation")
        return decoded

    def cli(self, url, *, command="download", notification=None, media=None, existing_file="overwrite", extra=()):
        notification = notification or self.notification()
        config = {
            "storage": {"data_root": str(self.root / "data")},
            "plugins": {"root": str(self.root / "plugins")},
            "notification": {
                "enabled": notification.enabled,
                "methods": notification.methods,
                "notify_on": notification.notify_on,
                "routes": dict(notification.routes),
                "email": notification.email.model_dump(by_alias=True),
            },
            "network": {"max_attempts": 1},
            "output": {"directory_format": "gallery", "existing_file": existing_file},
            "media": media or {"input_validation": "decode"},
        }
        config_path = self.root / "app.yaml"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONIOENCODING="utf-8")
        arguments = [
            sys.executable,
            "-m",
            "image_downloader",
            command,
            url,
            "--config",
            str(config_path),
            "--output-dir",
            str(self.root / "out"),
            "--fallback-generic",
            "enabled",
            "--json",
            *extra,
        ]
        result = subprocess.run(
            arguments, cwd=self.root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30, check=False
        )
        self.report["cli"].append({"exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
        assert result.stdout.strip(), result.stderr
        payload = json.loads(result.stdout)
        logs = "\n".join(p.read_text(encoding="utf-8") for p in (self.root / "data").rglob("debug.log"))
        self.report["cli"][-1]["log"] = logs
        return result.returncode, payload, logs


@pytest.fixture(scope="session")
def mailpit_report_dir():
    # Explicit opt-in means a missing service is a test failure, not a skip.
    with smtplib.SMTP("localhost", 1025, timeout=5) as smtp:
        assert smtp.ehlo()[0] == 250
    with httpx.Client(trust_env=False, timeout=5) as client:
        response = client.get(f"{API}/api/v1/messages", params={"limit": 1})
        response.raise_for_status()
        assert "messages" in response.json()
    path = ROOT / ".runtime" / "mailpit" / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8])
    path.mkdir(parents=True)
    print(f"\nMailpit reports: {path}")
    return path


@pytest.fixture
def mail_case(request, tmp_path, mailpit_report_dir):
    case = MailCase(request.node.name, tmp_path, mailpit_report_dir)
    yield case
    case.client.close()


@contextmanager
def gallery(mode):
    output = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(output, "PNG")
    counts = Counter()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            counts[self.path] += 1
            status, content_type = 200, "image/png"
            if self.path == "/gallery":
                paths = ["good.png", "bad.png"] if mode == "partial" else ["good.png"]
                body = ("<title>Mailpit test</title>" + "".join(f'<img src="/{p}">' for p in paths)).encode()
                content_type = "text/html"
            elif self.path == "/bad.png":
                status, body = 404, b"missing"
            elif mode == "decode":
                body = b"not a PNG"
            elif mode == "always_fail" or (mode == "retry" and counts[self.path] == 1):
                status, body = 503, b"retry later"
            else:
                body = output.getvalue()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/gallery", counts
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@contextmanager
def rejecting_smtp():
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.connection.settimeout(5)
            self.wfile.write(b"220 local rejection test\r\n")
            while line := self.rfile.readline():
                command = line.decode("ascii").split()[0].upper()
                if command == "QUIT":
                    self.wfile.write(b"221 goodbye\r\n")
                    return
                self.wfile.write(b"550 test rejection\r\n" if command == "MAIL" else b"250 OK\r\n")

    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_mailpit_smtp_unicode(mail_case):
    config = mail_case.notification()
    assert asyncio.run(EmailNotificationSender(config.email).send("メール通知テスト", "日本語の本文\nSMTP受信確認"))
    message, body = mail_case.expect_mail(1)[0]
    assert message["Subject"] == "メール通知テスト"
    assert body.strip() == "日本語の本文\nSMTP受信確認"
    mail_case.check("Japanese subject/body and sender/recipient match")


@pytest.mark.parametrize(
    "mode,code,label",
    [
        ("success", 0, "download_success"),
        ("partial", 5, "download_partial_success"),
        ("decode", 1, "image_processing_failed"),
        ("conflict", 1, "image_save_failed"),
    ],
)
def test_mailpit_download_cli(mail_case, mode, code, label):
    target = mail_case.root / "out" / "gallery" / "0001.png"
    if mode == "conflict":
        target.parent.mkdir(parents=True)
        target.write_bytes(b"existing file")
    with gallery(mode) as (url, _counts):
        status, payload, logs = mail_case.cli(url, existing_file="error" if mode == "conflict" else "overwrite")
    assert status == code, mail_case.report["cli"]
    assert (len(payload["saved"]), len(payload["failures"])) == {
        "success": (1, 0),
        "partial": (1, 1),
        "decode": (0, 1),
        "conflict": (0, 1),
    }[mode]
    body = mail_case.expect_mail(1)[0][1]
    assert f"{label}: count=1" in body
    assert "event=notification_sent" in logs
    if mode in {"success", "partial"}:
        with Image.open(target) as image:
            assert image.size == (2, 2)
    if mode == "partial":
        assert "image_fetch_failed: count=1" in body and "http_status_error" in body
        assert payload["failures"][0]["http_status"] == 404
    if mode in {"decode", "conflict"}:
        expected = "image_decode_error" if mode == "decode" else "existing_file_conflict"
        assert payload["failures"][0]["code"] == expected
        assert expected in body
    if mode == "conflict":
        assert target.read_bytes() == b"existing file"
    mail_case.check(f"CLI exit {code}, JSON, saved bytes, diagnostics and {label} notification agree")


def test_mailpit_aggregation_redaction(mail_case):
    async def scenario():
        logger = DownloadLogger()
        events = EventBus(logger)
        config = mail_case.notification()
        service = NotificationService(config, logger, events, {"email": EmailNotificationSender(config.email)})
        try:
            for index in range(6, 0, -1):
                await events.emit(
                    EventName.FETCH_FAILED,
                    EventPayload(
                        url=f"https://user:fake-password@example.test/{index}?token=fake-token#access_token=fake-fragment",
                        error="raw fake-exception",
                        error_code="http_status_error",
                        error_class="HttpStatusError",
                        chapter_id="1",
                        image_index=index,
                        stage="image_fetch",
                        http_status=404,
                    ),
                )
            assert mail_case.messages() == []
            await service.flush(source_url="https://example.test/gallery?token=fake-token")
            await service.flush(source_url="https://example.test/gallery")
        finally:
            await logger.close()

    asyncio.run(scenario())
    body = mail_case.expect_mail(1)[0][1]
    assert "image_fetch_failed: count=6" in body
    assert "reason_code: http_status_error count=6" in body
    assert body.count("example ") == 5
    assert "image=6" not in body
    assert body.index("image=1") < body.index("image=5")
    for secret in ("fake-password", "fake-token", "fake-fragment", "fake-exception"):
        assert secret not in body
    assert "[REDACTED]" in body
    mail_case.check("no email before flush; six failures; five sorted examples; secrets removed; flush idempotent")


@pytest.mark.parametrize("mode", ["disabled", "unselected", "route"])
def test_mailpit_selection_routes(mail_case, mode):
    async def scenario():
        logger = DownloadLogger()
        events = EventBus(logger)
        values = {"enabled": False} if mode == "disabled" else {"notify_on": ["save_error"]}
        if mode == "route":
            values = {"methods": ["desktop"], "notify_on": ["fetch_error"], "routes": {"fetch_error": ["email"]}}
        config = mail_case.notification(**values)

        class ForbiddenDesktop:
            called = False

            async def send(self, title, message):
                self.called = True
                raise AssertionError("desktop must not be selected")

        desktop = ForbiddenDesktop()
        failures = []
        events.on(EventName.NOTIFICATION_FAILED, failures.append)
        service = NotificationService(
            config, logger, events, {"email": EmailNotificationSender(config.email), "desktop": desktop}
        )
        try:
            await events.emit(EventName.FETCH_FAILED, EventPayload(url="https://example.test"))
            await service.flush(source_url="https://example.test")
            assert not desktop.called
            assert failures == []
        finally:
            await logger.close()

    asyncio.run(scenario())
    mail_case.expect_mail(1 if mode == "route" else 0)
    mail_case.check(f"{mode} selection honored; desktop not called")


def test_mailpit_smtp_failure_isolation(mail_case):
    with rejecting_smtp() as port:
        base = mail_case.notification()
        email = base.email.model_dump(by_alias=True)
        email.update(smtp_host="127.0.0.1", smtp_port=port)
        config = mail_case.notification(email=email)

        async def scenario():
            logger = DownloadLogger([DebugFileSink(mail_case.root / "failure.log")])
            events = EventBus(logger)
            observed = []
            events.on(EventName.NOTIFICATION_FAILED, lambda p: observed.append(("failed", p.channel)))
            events.on(EventName.NOTIFICATION_SENT, lambda p: observed.append(("sent", p.channel)))
            service = NotificationService(config, logger, events, {"email": EmailNotificationSender(config.email)})
            try:
                await events.emit(EventName.DOWNLOAD_SUCCESS)
                await service.flush(source_url="https://example.test")
            finally:
                await logger.close()
            return observed

        assert asyncio.run(scenario()) == [("failed", "email")]
        assert "event=notification_failed" in (mail_case.root / "failure.log").read_text(encoding="utf-8")
        with gallery("success") as (url, _counts):
            status, payload, logs = mail_case.cli(url, notification=config)
        assert status == 0 and len(payload["saved"]) == 1
        with Image.open(mail_case.root / "out" / "gallery" / "0001.png") as image:
            assert image.size == (2, 2)
        assert logs.count("event=notification_failed") == 1
        assert "event=notification_sent" not in logs
    mail_case.expect_mail(0)
    mail_case.check("real SMTP 550 produces one failure event; CLI download remains successful")


@pytest.mark.parametrize("mode,code", [("retry", 0), ("always_fail", 1)])
def test_mailpit_workflow_final_only(mail_case, mode, code):
    with gallery(mode) as (url, counts):
        status, payload, logs = mail_case.cli(
            url,
            command="workflow",
            extra=("--workflow-retries", "1", "--workflow-retry-delay", "0"),
        )
        assert counts["/good.png"] == 2
    assert status == code, mail_case.report["cli"]
    body = mail_case.expect_mail(1)[0][1]
    assert logs.count("event=notification_sent") == 1
    item = payload["items"][0]
    # Continued image errors return a settled DownloadResult, hence item partial;
    # the CLI separately returns 1 when there are no saved/skipped files.
    assert item["status"] == ("success" if mode == "retry" else "partial")
    if mode == "retry":
        assert "download_success: count=1" in body
        assert "image_fetch_failed" not in body
        assert len(item["download"]["saved"]) == 1
    else:
        assert item["download"]["saved"] == []
        assert item["download"]["skipped"] == []
        assert len(item["download"]["failures"]) == 1
        assert item["download"]["failures"][0]["http_status"] == 503
        assert "image_fetch_failed: count=1" in body
        assert "reason_code: http_status_error count=1" in body
        assert "download_success" not in body
    mail_case.check(f"two HTTP attempts; CLI exit {code}; one final notification without attempt duplication")
