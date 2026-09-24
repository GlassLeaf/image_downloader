from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

from image_downloader.config import Notification
from image_downloader.observability import notifications
from image_downloader.observability.events import EventBus, EventName, EventPayload
from image_downloader.observability.logging import DebugFileSink, DownloadLogger
from image_downloader.observability.notifications import (
    DesktopNotificationSender,
    NotificationService,
    _send_mail,
)


class CapturingSender:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.messages: list[str] = []

    async def send(self, _title: str, message: str) -> bool:
        if self.error is not None:
            raise self.error
        self.messages.append(message)
        return True


def _notification(**values: object) -> Notification:
    return Notification.model_validate(values)


def test_notification_collects_typed_events_masks_secrets_and_uses_injected_senders(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[str, str, int]:
        debug_path = tmp_path / "debug.log"
        logger = DownloadLogger([DebugFileSink(debug_path)])
        events = EventBus(logger)
        desktop = CapturingSender()
        email = CapturingSender(error=RuntimeError("mail delivery unavailable"))
        service = NotificationService(
            _notification(
                enabled=True,
                methods=["desktop", "email"],
                notify_on=["fetch_error"],
            ),
            logger,
            events,
            {"desktop": desktop, "email": email},
        )
        await events.emit(
            EventName.FETCH_FAILED,
            EventPayload(
                url=("https://user:password@example.test/page?token=secret-value#access_token=fragment-secret"),
                error="https://example.test/image authorization: hidden-value",
            ),
        )
        await service.flush(
            source_url=("https://user:password@example.test/page?token=secret-value#access_token=fragment-secret"),
            log_path=str(debug_path),
        )
        await logger.close()
        return desktop.messages[0], debug_path.read_text(encoding="utf-8"), len(email.messages)

    message, debug_log, email_messages = asyncio.run(scenario())
    assert "secret-value" not in message
    assert "fragment-secret" not in message
    assert "password" not in message
    assert "hidden-value" not in message
    assert "[REDACTED]" in message
    assert "event=notification_sent module=notification" in debug_log
    assert email_messages == 0
    assert "event=notification_failed module=notification error=UnknownError" in debug_log


def test_notification_routes_override_default_methods() -> None:
    async def scenario() -> tuple[int, int]:
        logger = DownloadLogger()
        events = EventBus(logger)
        desktop = CapturingSender(error=RuntimeError("desktop unavailable"))
        email = CapturingSender()
        service = NotificationService(
            _notification(
                enabled=True,
                methods=["desktop"],
                routes={"fetch_error": ["email"]},
                notify_on=["fetch_error"],
            ),
            logger,
            events,
            {"desktop": desktop, "email": email},
        )
        await events.emit(
            EventName.FETCH_FAILED,
            EventPayload(url="https://example.test", error="failure"),
        )
        await service.flush(source_url="https://example.test")
        await logger.close()
        return len(desktop.messages), len(email.messages)

    assert asyncio.run(scenario()) == (0, 1)


def test_image_failure_notification_groups_reasons_and_limits_sorted_examples() -> None:
    async def scenario() -> str:
        logger = DownloadLogger()
        events = EventBus(logger)
        desktop = CapturingSender()
        service = NotificationService(
            _notification(enabled=True, methods=["desktop"], notify_on=["process_error"]),
            logger,
            events,
            {"desktop": desktop},
        )
        for index in range(6, 0, -1):
            await events.emit(
                EventName.IMAGE_PROCESS_FAILED,
                EventPayload(
                    url=f"https://images.test/{index}.jpg?token=secret-{index}",
                    response_url=f"https://cdn.test/{index}.jpg?token=secret-{index}",
                    stage="image_processing",
                    chapter_id="2",
                    image_index=index,
                    http_status=200,
                    error_code="image_decode_error" if index < 6 else "image_mime_mismatch",
                    error_reason="token=reason-secret",
                    error_class="ImageDecodeError",
                ),
            )
        await service.flush(source_url="https://example.test/gallery")
        await logger.close()
        return desktop.messages[0]

    message = asyncio.run(scenario())
    assert "image_processing_failed: count=6" in message
    assert "reason_code: image_decode_error count=5" in message
    assert "reason_code: image_mime_mismatch count=1" in message
    assert message.count("example ") == 5
    assert message.index("image=1") < message.index("image=2") < message.index("image=3")
    assert "transport: completed" in message
    assert "image data cannot be decoded" in message


def test_runtime_notification_displays_safe_code_reason_and_operation() -> None:
    async def scenario() -> str:
        logger = DownloadLogger()
        events = EventBus(logger)
        desktop = CapturingSender()
        service = NotificationService(
            _notification(enabled=True, methods=["desktop"], notify_on=["runtime_error"]),
            logger,
            events,
            {"desktop": desktop},
        )
        await events.emit(
            EventName.RUNTIME_FAILED,
            EventPayload(
                url="https://example.test/gallery?token=secret",
                error_code="unexpected_runtime_error",
                error_class="RuntimeError",
                operation="download",
            ),
        )
        await service.flush(source_url="https://example.test/gallery?token=secret")
        await logger.close()
        return desktop.messages[0]

    message = asyncio.run(scenario())
    assert "runtime_error: count=1" in message
    assert "reason_code: unexpected_runtime_error count=1" in message
    assert "operation: download" in message
    assert "reason: unexpected runtime failure" in message
    assert "token=secret" not in message
    assert "secret-" not in message
    assert "reason-secret" not in message


def test_authentication_success_notifications_are_opt_in() -> None:
    async def scenario() -> tuple[int, int]:
        logger = DownloadLogger()
        default_events = EventBus(logger)
        default_sender = CapturingSender()
        default_service = NotificationService(
            _notification(enabled=True, methods=["desktop"], notify_on=["auth_error"]),
            logger,
            default_events,
            {"desktop": default_sender},
        )
        await default_events.emit(
            EventName.LOGIN_SUCCESS,
            EventPayload(url="https://example.test/login"),
        )
        await default_service.flush(source_url="https://example.test")

        enabled_events = EventBus(logger)
        enabled_sender = CapturingSender()
        enabled_service = NotificationService(
            _notification(
                enabled=True,
                methods=["desktop"],
                notify_on=["auth_login_success"],
            ),
            logger,
            enabled_events,
            {"desktop": enabled_sender},
        )
        await enabled_events.emit(
            EventName.LOGIN_SUCCESS,
            EventPayload(url="https://example.test/login"),
        )
        await enabled_service.flush(source_url="https://example.test")
        await logger.close()
        return len(default_sender.messages), len(enabled_sender.messages)

    assert asyncio.run(scenario()) == (0, 1)


def test_event_observer_failure_is_isolated_and_safely_logged(tmp_path: Path) -> None:
    async def scenario() -> tuple[list[EventPayload], str]:
        debug_path = tmp_path / "debug.log"
        logger = DownloadLogger([DebugFileSink(debug_path)])
        events = EventBus(logger)
        received: list[EventPayload] = []

        async def failing(_payload: EventPayload) -> None:
            raise RuntimeError("token=observer-secret")

        events.on(EventName.FETCH_FAILED, failing)
        events.on(EventName.FETCH_FAILED, received.append)
        await events.emit(
            EventName.FETCH_FAILED,
            EventPayload(
                url="https://example.test/page?token=request-secret",
                error="payload-secret",
                error_class="RuntimeError",
            ),
        )
        await logger.close()
        return received, debug_path.read_text(encoding="utf-8")

    received, debug_log = asyncio.run(scenario())
    assert received == [
        EventPayload(
            url="https://example.test/page?token=[REDACTED]",
            error="[REDACTED]",
            error_class="UnknownError",
        )
    ]
    assert "event=event_observer_failed module=runtime" in debug_log
    assert "observer-secret" not in debug_log
    assert "request-secret" not in debug_log
    assert "payload-secret" not in debug_log


class FakeSmtpClient:
    def __init__(self) -> None:
        self.started_tls = False
        self.login_args: tuple[str, str] | None = None
        self.sent = False

    def __enter__(self) -> FakeSmtpClient:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

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
        {
            "smtp_host": "mail.test",
            "smtp_port": 587,
            "use_tls": True,
            "from": "from@test",
            "to": ["to@test"],
        },
        "title",
        "body",
    )
    assert implicit_tls.sent and implicit_tls.login_args == ("user", "password")
    assert not implicit_tls.started_tls
    assert starttls.sent and starttls.started_tls


def test_desktop_notification_sender_uses_optional_dependency_locally(monkeypatch) -> None:
    sent: list[tuple[str, str]] = []

    class FakeNotifier:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def send(self, *, title: str, message: str) -> None:
            sent.append((title, message))

    module = types.ModuleType("desktop_notifier")
    module.DesktopNotifier = FakeNotifier
    monkeypatch.setitem(sys.modules, "desktop_notifier", module)
    sender = DesktopNotificationSender()
    assert asyncio.run(sender.send("title", "body"))
    assert sent == [("title", "body")]
