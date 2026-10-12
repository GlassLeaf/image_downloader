from __future__ import annotations

import asyncio
import io
import smtplib
import threading
from datetime import UTC, datetime
from email.message import EmailMessage

import httpx
import pytest
from PIL import Image

import image_downloader.cli as cli
import image_downloader.commands.download as cli_download
from image_downloader import AppConfig, RuntimeComposer, UpdateCandidate, UpdateSnapshot
from image_downloader.commands.workflow import _status
from image_downloader.config import Notification
from image_downloader.observability import notifications
from image_downloader.observability.events import EventBus, EventName, EventPayload
from image_downloader.observability.logging import ChapterFileSink, ConsoleSink, DebugFileSink, DownloadLogger
from image_downloader.observability.notifications import EmailNotificationSender, NotificationService
from image_downloader.plugins.builtin import GenericHtmlPlugin

REFUSED = {
    "hidden-one@example.net": (550, b"token=response-secret"),
    "hidden-two@EXAMPLE.NET": (450, b"password=response-secret"),
    '"hidden@local"@example.org': (550, b"private response"),
}


class FakeSmtp:
    def __init__(self, mode: str, recipients=None, *, failure_stage=None) -> None:
        self.mode = mode
        self.recipients = REFUSED if recipients is None else recipients
        self.refusal = smtplib.SMTPRecipientsRefused(self.recipients)
        self.failure = RuntimeError("token=stage-secret")
        self.failure_stage = failure_stage
        self.calls: list[str] = []
        self.messages: list[EmailMessage] = []
        self.exit_type = None

    def _call(self, stage):
        self.calls.append(stage)
        if self.failure_stage == stage:
            raise self.failure

    def __enter__(self):
        self._call("enter")
        return self

    def __exit__(self, exc_type, *_args):
        self.exit_type = exc_type
        self._call("close")

    def starttls(self, **_kwargs):
        self._call("starttls")

    def login(self, *_args):
        self._call("login")

    def send_message(self, message):
        self._call("send")
        self.messages.append(message)
        if self.mode == "all":
            raise self.refusal
        return self.recipients if self.mode == "partial" else {}


def _fake_smtp(monkeypatch, mode, recipients=None, **kwargs):
    client = FakeSmtp(mode, recipients, **kwargs)
    connections = []

    def connect(*args, **options):
        connections.append((args, options))
        return client

    monkeypatch.setattr(notifications.smtplib, "SMTP", connect)
    monkeypatch.setattr(notifications.smtplib, "SMTP_SSL", connect)
    return client, connections


def _notification(**email):
    return Notification.model_validate(
        {
            "enabled": True,
            "methods": ["email", "desktop"],
            "notify_on": ["download_success"],
            "email": {
                "smtp_host": "SMTP.Server.Domain:2525",
                "smtp_port": 2525,
                "use_tls": False,
                "from": "sender@example.test",
                "to": ["accepted@example.test", "hidden-one@example.net"],
                **email,
            },
        }
    )


@pytest.mark.parametrize("mode", ["success", "partial", "all"])
@pytest.mark.parametrize("port,tls", [(465, True), (587, True), (2525, False)])
def test_sender_result_and_cleanup_across_tls_modes(monkeypatch, mode, port, tls):
    client, connections = _fake_smtp(monkeypatch, mode)
    sender = EmailNotificationSender(_notification(smtp_port=port, use_tls=tls).email)
    if mode == "success":
        assert asyncio.run(sender.send("日本語の件名", "日本語の本文")) is True
    else:
        error_type = notifications._PartialRecipientRefusalError if mode == "partial" else smtplib.SMTPRecipientsRefused
        with pytest.raises(error_type) as raised:
            asyncio.run(sender.send("日本語の件名", "日本語の本文"))
        if mode == "all":
            assert raised.value is client.refusal
            assert client.exit_type is smtplib.SMTPRecipientsRefused
        else:
            assert client.exit_type is None  # Partial rejection is raised after SMTP exit.
            assert str(raised.value) == "some SMTP recipients were refused"
            assert raised.value.refused_count == 3
            assert raised.value.refused_domains == ("example.net", "example.org")
            assert set(vars(raised.value)) == {"refused_count", "refused_domains"}
    assert client.calls.count("send") == client.calls.count("close") == len(connections) == 1
    assert ("starttls" in client.calls) is (tls and port != 465)
    assert connections[0][1]["timeout"] == 30
    assert ("context" in connections[0][1]) is (tls and port == 465)
    assert client.messages[0]["Subject"] == "日本語の件名"
    assert client.messages[0].get_content().strip() == "日本語の本文"


@pytest.mark.parametrize(
    "recipient,domain",
    [
        ('"hidden@local"@EXAMPLE.TEST', "example.test"),
        ("hidden@例え.テスト", "xn--r8jz45g.xn--zckzah"),
        ("hidden@[192.0.2.1]", "192.0.2.1"),
        ("hidden@[IPv6:2001:0db8::1]", "2001:db8::1"),
        ("hidden@example.test\nFORGED", "unknown"),
        ("hidden\x00@example.test", "unknown"),
        ("hidden", "unknown"),
        ("Name <hidden@example.test>", "unknown"),
        ("hidden@example.test/secret", "unknown"),
        ("hidden@[not-an-ip]", "unknown"),
        ("(" * 500 + "x" + ")" * 500 + "hidden@example.test", "unknown"),
    ],
)
def test_refusal_domains_are_parsed_and_never_fall_back_to_raw_text(recipient, domain):
    assert notifications._refused_domains({recipient: (550, b"private")}) == (domain,)


@pytest.mark.parametrize(
    "host,expected",
    [
        ("SMTP.Example.TEST:2525", "smtp.example.test"),
        ("smtp.example.test.", "smtp.example.test"),
        ("smtp.例え.テスト", "smtp.xn--r8jz45g.xn--zckzah"),
        ("192.0.2.1:2525", "192.0.2.1"),
        ("[2001:0db8::1]:2525", "2001:db8::1"),
        ("2001:0db8::1", "2001:db8::1"),
        ("localhost", "localhost"),
        ("smtp.example.test\r\nFORGED", "unknown"),
        ("user:password@smtp.example.test", "unknown"),
        ("https://smtp.example.test/secret", "unknown"),
        ("smtp.example.test:65536", "unknown"),
        ("smtp.example.test:-1", "unknown"),
        ("[2001:db8::1]:x", "unknown"),
        ("[not-an-ip]", "unknown"),
        ("fe80::1%private-zone", "unknown"),
        ("", "unknown"),
    ],
)
def test_smtp_host_diagnostics_are_normalized_without_network_lookup(host, expected):
    assert notifications._smtp_diagnostic_host(host) == expected


@pytest.mark.parametrize("mode", ["success", "partial", "all"])
def test_refusal_logs_and_events_do_not_duplicate_or_expose_mailbox_secrets(tmp_path, monkeypatch, mode):
    client, _ = _fake_smtp(monkeypatch, mode)

    class Desktop:
        called = 0

        async def send(self, *_args):
            self.called += 1
            return True

    async def scenario():
        stream = io.StringIO()
        logger = DownloadLogger(
            [ConsoleSink(stream), ChapterFileSink(tmp_path / "normal.log"), DebugFileSink(tmp_path / "debug.log")]
        )
        events = EventBus(logger)
        failed, sent = [], []
        events.on(EventName.NOTIFICATION_FAILED, failed.append)
        events.on(EventName.NOTIFICATION_SENT, sent.append)
        config, desktop = _notification(), Desktop()
        service = NotificationService(
            config, logger, events, {"email": EmailNotificationSender(config.email), "desktop": desktop}
        )
        try:
            await events.emit(EventName.DOWNLOAD_SUCCESS)
            await service.flush(source_url="https://example.test")
            await service.flush(source_url="https://example.test")
            assert desktop.called == 1
        finally:
            await logger.close()
        return stream.getvalue(), failed, sent

    console, failed, sent = asyncio.run(scenario())
    assert client.calls.count("send") == 1
    assert [payload.channel for payload in sent] == (["email", "desktop"] if mode == "success" else ["desktop"])
    for text in (console, (tmp_path / "normal.log").read_text(), (tmp_path / "debug.log").read_text()):
        assert text.count("event=notification_failed") == (0 if mode == "success" else 1)
        if mode != "success":
            code = f"smtp_{'partial' if mode == 'partial' else 'all'}_recipients_refused"
            assert f"action={code} count=3 smtp_host=smtp.server.domain" in text
            assert 'refused_domains=["example.net","example.org"]' in text
        for secret in ("hidden-one", "hidden-two", "hidden@local", "response-secret", "private response", "sender@"):
            assert secret not in text
    if mode == "success":
        assert failed == []
    else:
        assert failed == [
            EventPayload(channel="email", error="[REDACTED]", error_class="UnknownError", error_code=code)
        ]
        assert "smtp.server.domain" not in repr(failed) and "example.net" not in repr(failed)


def test_invalid_diagnostic_addresses_and_smtp_host_do_not_hide_refusal(tmp_path, monkeypatch):
    client, _ = _fake_smtp(monkeypatch, "partial", {"hidden@example.test\nFORGED": (550, b"response-secret")})

    async def scenario():
        logger = DownloadLogger([ChapterFileSink(tmp_path / "normal.log"), DebugFileSink(tmp_path / "debug.log")])
        config = _notification(smtp_host="smtp.example.test\nFORGED")
        service = NotificationService(config, logger, None, {"email": EmailNotificationSender(config.email)})
        try:
            assert await service._deliver("email", "title", "body") is False
        finally:
            await logger.close()

    asyncio.run(scenario())
    assert client.calls.count("send") == 1
    for path in (tmp_path / "normal.log", tmp_path / "debug.log"):
        text = path.read_text()
        assert 'count=1 smtp_host=unknown refused_domains=["unknown"]' in text
        assert "FORGED" not in text and "hidden" not in text and "response-secret" not in text


def test_refusal_logs_the_host_used_before_async_settings_replacement(tmp_path, monkeypatch):
    client, _ = _fake_smtp(monkeypatch, "partial")
    started, proceed = threading.Event(), threading.Event()
    original = client.send_message

    def delayed(message):
        started.set()
        assert proceed.wait(10)
        return original(message)

    monkeypatch.setattr(client, "send_message", delayed)

    async def scenario():
        logger = DownloadLogger([DebugFileSink(tmp_path / "debug.log")])
        config = _notification()
        sender = EmailNotificationSender(config.email)
        service = NotificationService(config, logger, None, {"email": sender})
        task = asyncio.create_task(service._deliver("email", "title", "body"))
        try:
            assert await asyncio.to_thread(started.wait, 10)
            sender.config = _notification(smtp_host="different.example.test").email
            proceed.set()
            assert await task is False
        finally:
            proceed.set()
            await task
            await logger.close()

    asyncio.run(scenario())
    text = (tmp_path / "debug.log").read_text()
    assert "smtp_host=smtp.server.domain" in text
    assert "different.example.test" not in text


@pytest.mark.parametrize("stage", ["starttls", "login", "send", "close"])
def test_existing_smtp_stage_failures_keep_original_exception(monkeypatch, stage):
    client, _ = _fake_smtp(monkeypatch, "partial", failure_stage=stage)
    settings = _notification(use_tls=True, smtp_port=587, username="user").email.model_dump(by_alias=True)
    settings["password"] = "credential-secret"
    with pytest.raises(RuntimeError) as raised:
        notifications._send_mail(settings, "title", "body")
    assert raised.value is client.failure
    assert client.calls.count("close") == 1
    assert notifications._smtp_refusal_diagnostic(raised.value) is None


@pytest.mark.parametrize("mode", ["partial", "all"])
@pytest.mark.parametrize("operation", ["download", "workflow", "cli"])
def test_smtp_refusal_preserves_saved_results_and_cli_status(tmp_path, monkeypatch, mode, operation):
    client, _ = _fake_smtp(monkeypatch, mode)

    class Gallery(GenericHtmlPlugin):
        async def check_updates(self, url, context):
            return UpdateSnapshot(
                url, (UpdateCandidate("https://example.test/gallery", "gallery", "1"),), datetime.now(UTC)
            )

    async def scenario():
        config = AppConfig.model_validate(
            {
                "storage": {"data_root": str((tmp_path / "data").resolve())},
                "plugins": {"root": str((tmp_path / "plugins").resolve())},
                "logging": {"console": {"enabled": False}},
                "notification": _notification()
                .model_copy(update={"methods": ("email",)})
                .model_dump(by_alias=True, warnings=False),
            }
        )
        service = RuntimeComposer(
            config, config_root=tmp_path.resolve(), plugin_root=(tmp_path / "plugins").resolve()
        ).compose()
        record = service.registry.records["core.generic-html"]
        service.registry.resolve = lambda *_args, **_kwargs: (record, Gallery())
        data = io.BytesIO()
        Image.new("RGB", (2, 2), "red").save(data, "PNG")
        await service.gateway.client.aclose()
        service.gateway.client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: (
                    httpx.Response(200, text="<img src='/one.png'>")
                    if request.url.path == "/gallery"
                    else httpx.Response(200, content=data.getvalue(), headers={"content-type": "image/png"})
                )
            )
        )
        observed = []
        service.events.on(EventName.NOTIFICATION_FAILED, observed.append)
        try:
            if operation == "workflow":
                result = await service.workflow("https://example.test/feed", workflow_retries=0)
                assert _status(result) == cli.EXIT_SUCCESS
                assert result.items[0].status == "success"
                download = result.items[0].download
                assert download is not None and not download.failures and len(download.saved_files) == 1
            elif operation == "download":
                result = await service.run("https://example.test/gallery")
                assert not result.failures and len(result.saved_files) == 1
            else:

                class Composer:
                    def __init__(self, *_args, **_kwargs):
                        pass

                    def compose(self):
                        return service

                monkeypatch.setattr(cli_download, "RuntimeComposer", Composer)
                monkeypatch.setattr(
                    cli_download, "_config_for", lambda *_args, **_kwargs: (config, tmp_path, tmp_path, "test")
                )
                args = cli.build_parser().parse_args(["download", "https://example.test/gallery"])
                assert await cli.run(args) == cli.EXIT_SUCCESS
            assert len(observed) == 1
            assert client.calls.count("send") == 1
        finally:
            await service.close()
        files = list((tmp_path / "data").rglob("*.png"))
        assert len(files) == 1
        with Image.open(files[0]) as image:
            assert image.size == (2, 2)

    asyncio.run(scenario())


def test_disabled_notifications_do_not_send_mail(monkeypatch):
    client, connections = _fake_smtp(monkeypatch, "partial")

    async def scenario():
        config = _notification().model_copy(update={"enabled": False})
        logger, events = DownloadLogger(), EventBus()
        service = NotificationService(config, logger, events, {"email": EmailNotificationSender(config.email)})
        await events.emit(EventName.DOWNLOAD_SUCCESS)
        await service.flush(source_url="https://example.test")
        await logger.close()

    asyncio.run(scenario())
    assert client.calls == connections == []
