from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

from image_downloader.observability import notifications
from image_downloader.observability.events import EventBus
from image_downloader.observability.logging import DebugFileSink, DownloadLogger
from image_downloader.observability.notifications import NotificationService, _send_mail


class CapturingNotificationService(NotificationService):
    def __init__(self, config: dict[str, object], logger: DownloadLogger, events: EventBus) -> None:
        super().__init__(config, logger, events)
        self.messages: list[str] = []
        self.email_calls = 0

    async def _desktop(self, _title: str, message: str) -> bool:
        self.messages.append(message)
        return True

    async def _email(self, _title: str, _message: str) -> bool:
        self.email_calls += 1
        raise RuntimeError("mail delivery unavailable")


def test_notification_collects_events_masks_secrets_and_uses_all_methods(tmp_path: Path) -> None:
    async def scenario() -> tuple[str, str]:
        debug_path = tmp_path / "debug.log"
        logger = DownloadLogger([DebugFileSink(debug_path)])
        events = EventBus()
        service = CapturingNotificationService(
            {"notification": {"enabled": True, "methods": ["desktop", "email"], "notify_on": ["fetch_error"]}},
            logger,
            events,
        )
        await events.emit(
            "on_fetch_failed",
            url="https://user:password@example.test/page?token=secret-value#access_token=fragment-secret",
            error="https://example.test/image authorization: hidden-value",
        )
        await service.flush(
            source_url="https://user:password@example.test/page?token=secret-value#access_token=fragment-secret",
            log_path=str(debug_path),
        )
        await logger.close()
        return service.messages[0], debug_path.read_text(encoding="utf-8"), service.email_calls

    message, debug_log, email_calls = asyncio.run(scenario())
    assert "secret-value" not in message
    assert "fragment-secret" not in message
    assert "password" not in message
    assert "hidden-value" not in message
    assert "[REDACTED]" in message
    assert "event=notification_sent module=notification" in debug_log
    assert email_calls == 1
    assert "event=notification_failed module=notification error=UnknownError" in debug_log


class DesktopFailureService(CapturingNotificationService):
    async def _desktop(self, _title: str, _message: str) -> bool:
        raise RuntimeError("desktop unavailable")

    async def _email(self, _title: str, message: str) -> bool:
        self.email_calls += 1
        self.messages.append(message)
        return True


def test_notification_routes_can_override_default_methods(tmp_path: Path) -> None:
    async def scenario() -> tuple[int, str]:
        logger = DownloadLogger([DebugFileSink(tmp_path / "debug.log")])
        events = EventBus()
        service = DesktopFailureService(
            {
                "notification": {
                    "enabled": True,
                    "methods": ["desktop"],
                    "routes": {"fetch_error": ["email"]},
                    "notify_on": ["fetch_error"],
                }
            },
            logger,
            events,
        )
        await events.emit("on_fetch_failed", url="https://example.test", error="failure")
        await service.flush(source_url="https://example.test")
        await logger.close()
        return service.email_calls, (tmp_path / "debug.log").read_text(encoding="utf-8")

    email_calls, debug_log = asyncio.run(scenario())
    assert email_calls == 1
    assert "event=notification_sent module=notification" in debug_log


def test_authentication_success_notifications_are_opt_in(tmp_path: Path) -> None:
    async def scenario() -> tuple[int, int]:
        logger = DownloadLogger()
        events = EventBus()
        default_service = CapturingNotificationService(
            {"notification": {"enabled": True, "methods": ["desktop"], "notify_on": ["auth_error"]}},
            logger,
            events,
        )
        await events.emit("on_login_success", url="https://example.test/login")
        await default_service.flush(source_url="https://example.test")

        enabled_events = EventBus()
        enabled_service = CapturingNotificationService(
            {"notification": {"enabled": True, "methods": ["desktop"], "notify_on": ["auth_login_success"]}},
            logger,
            enabled_events,
        )
        await enabled_events.emit("on_login_success", url="https://example.test/login")
        await enabled_service.flush(source_url="https://example.test")
        return len(default_service.messages), len(enabled_service.messages)

    disabled_count, enabled_count = asyncio.run(scenario())
    assert disabled_count == 0
    assert enabled_count == 1


class FakeSmtpClient:
    def __init__(self) -> None:
        self.started_tls = False
        self.login_args: tuple[str, str] | None = None
        self.sent = False

    def __enter__(self) -> FakeSmtpClient:
        return self

    def __exit__(self, *_args: object) -> None:
        pass

    def starttls(self, **_kwargs: object) -> None:
        self.started_tls = True

    def login(self, username: str, password: str) -> None:
        self.login_args = (username, password)

    def send_message(self, _message: object) -> None:
        self.sent = True


def test_notification_smtp_tls_modes_are_local(monkeypatch) -> None:
    implicit_tls = FakeSmtpClient()
    starttls = FakeSmtpClient()
    monkeypatch.setattr(notifications.smtplib, "SMTP_SSL", lambda *_args, **_kwargs: implicit_tls)
    monkeypatch.setattr(notifications.smtplib, "SMTP", lambda *_args, **_kwargs: starttls)

    _send_mail(
        {
            "smtp_host": "mail.test",
            "smtp_port": 465,
            "use_tls": True,
            "from": "from@test",
            "to": ["to@test"],
            "username": "user",
            "password": "password",
        },
        "title",
        "body",
    )
    _send_mail(
        {"smtp_host": "mail.test", "smtp_port": 587, "use_tls": True, "from": "from@test", "to": ["to@test"]},
        "title",
        "body",
    )

    assert implicit_tls.sent and implicit_tls.login_args == ("user", "password")
    assert not implicit_tls.started_tls
    assert starttls.sent and starttls.started_tls


def test_desktop_notification_uses_optional_dependency_locally(monkeypatch) -> None:
    sent: list[tuple[str, str]] = []

    class FakeNotifier:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def send(self, *, title: str, message: str) -> None:
            sent.append((title, message))

    module = types.ModuleType("desktop_notifier")
    module.DesktopNotifier = FakeNotifier
    monkeypatch.setitem(sys.modules, "desktop_notifier", module)
    logger = DownloadLogger()
    service = NotificationService({"notification": {"enabled": True, "methods": ["desktop"]}}, logger)
    assert asyncio.run(service._desktop("title", "body"))
    assert sent == [("title", "body")]
