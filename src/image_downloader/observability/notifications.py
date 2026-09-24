from __future__ import annotations

import asyncio
import importlib.util
import os
import smtplib
import ssl
from collections import Counter
from collections.abc import Mapping
from email.message import EmailMessage
from functools import partial
from typing import Any, Protocol

from ..configuration.models import Email, Notification, NotificationCategory, NotificationMethod
from ..credentials.keyring import load_secret
from ..exceptions import ConfigurationError
from .events import EventBus, EventName, EventPayload
from .logging import DownloadLogger

NOTIFICATION_SOURCES: Mapping[EventName, NotificationCategory] = {
    EventName.FETCH_FAILED: "fetch_error",
    EventName.REQUEST_FAILED: "fetch_error",
    EventName.SAVE_FAILED: "save_error",
    EventName.AUTH_FAILED: "auth_error",
    EventName.PARSE_FAILED: "parse_error",
    EventName.IMAGE_PROCESS_FAILED: "process_error",
    EventName.PLUGIN_FAILED: "plugin_error",
    EventName.CONFIG_FAILED: "config_error",
    EventName.STORAGE_FAILED: "storage_error",
    EventName.RUNTIME_FAILED: "runtime_error",
    EventName.UPDATE_FAILED: "update_error",
    EventName.COOKIE_STORE_ACCESS: "auth_cookie_store_access",
    EventName.CREDENTIAL_STORE_ACCESS: "auth_credential_store_access",
    EventName.LOGIN_SUCCESS: "auth_login_success",
    EventName.SESSION_REFRESH_SUCCESS: "auth_session_refresh_success",
    EventName.DOWNLOAD_SUCCESS: "download_success",
    EventName.DOWNLOAD_PARTIAL_SUCCESS: "download_partial_success",
}

_IMAGE_NOTIFICATION_LABELS: Mapping[NotificationCategory, str] = {
    "fetch_error": "image_fetch_failed",
    "process_error": "image_processing_failed",
    "save_error": "image_save_failed",
}
_NOTIFICATION_EXAMPLE_LIMIT = 5
_NOTIFICATION_URL_LIMIT = 256
_NOTIFICATION_BODY_LIMIT = 4096


class NotificationSender(Protocol):
    async def send(self, title: str, message: str) -> bool: ...


def validate_notification_delivery(config: Notification) -> None:
    """Validate only the methods reachable from enabled notification categories."""
    if not config.enabled:
        return
    selected_routes = [config.routes.get(category, config.methods) for category in config.notify_on]
    if any(not route for route in selected_routes):
        raise ConfigurationError("enabled notification categories require at least one delivery method")
    methods = {method for route in selected_routes for method in route}
    if "desktop" in methods and importlib.util.find_spec("desktop_notifier") is None:
        raise ConfigurationError("desktop notification requires image-downloader[notify]")
    if "email" in methods and (not config.email.smtp_host or not config.email.from_ or not config.email.to):
        raise ConfigurationError("email notification requires smtp_host, from, and to")


class DesktopNotificationSender:
    async def send(self, title: str, message: str) -> bool:
        try:
            from desktop_notifier import DesktopNotifier
        except ImportError as exc:
            raise RuntimeError("desktop notification requires image-downloader[notify]") from exc
        notifier = DesktopNotifier(app_name="image-downloader")
        await notifier.send(title=title, message=message)
        return True


class EmailNotificationSender:
    def __init__(self, config: Email) -> None:
        self.config = config

    async def send(self, title: str, message: str) -> bool:
        settings = self.config.model_dump(by_alias=True, warnings=False)
        if not settings["smtp_host"] or not settings["from"] or not settings["to"]:
            raise RuntimeError("email notification requires smtp_host, from, and to")
        username = settings["username"]
        if username:
            password = os.getenv("IMAGE_DOWNLOADER_SMTP_PASSWORD") or load_secret(
                settings["credential_service"], username
            )
            if not password:
                raise RuntimeError(
                    "email notification requires SMTP credentials in keyring or IMAGE_DOWNLOADER_SMTP_PASSWORD"
                )
            settings["password"] = password
        await asyncio.to_thread(_send_mail, settings, title, message)
        return True


def default_notification_senders(config: Notification) -> Mapping[NotificationMethod, NotificationSender]:
    return {
        "desktop": DesktopNotificationSender(),
        "email": EmailNotificationSender(config.email),
    }


class NotificationService:
    """Collect configured events and deliver one summary after an operation."""

    def __init__(
        self,
        config: Notification,
        logger: DownloadLogger,
        events: EventBus | None,
        senders: Mapping[NotificationMethod, NotificationSender],
    ) -> None:
        self.config = config
        self.logger = logger
        self.events = events
        self.senders = senders
        self._records: list[tuple[NotificationCategory, EventPayload]] = []
        if events is not None:
            for source in NOTIFICATION_SOURCES:
                events.on(source, partial(self._collect, source))

    def _collect(self, source: EventName, payload: EventPayload) -> None:
        category = NOTIFICATION_SOURCES[source]
        if not self.config.enabled or category not in self.config.notify_on:
            return
        self._records.append((category, payload))

    async def flush(self, *, source_url: str, log_path: str | None = None) -> None:
        if not self._records:
            return
        grouped: dict[NotificationCategory, list[EventPayload]] = {}
        for category, payload in self._records:
            grouped.setdefault(category, []).append(payload)
        self._records.clear()
        title = "Image downloader notification"
        lines = [f"URL: {self.logger.safe_url(source_url)}"]
        examples: list[EventPayload] = []
        for category in sorted(grouped):
            payloads = sorted(grouped[category], key=self._payload_sort_key)
            label = _IMAGE_NOTIFICATION_LABELS.get(category, category)
            lines.append(f"{label}: count={len(payloads)}")
            if category in _IMAGE_NOTIFICATION_LABELS:
                reason_counts = Counter(payload.error_code or "unknown_image_failure" for payload in payloads)
                for code, count in sorted(reason_counts.items()):
                    lines.append(f"reason_code: {code} count={count}")
                examples.extend(payload for payload in payloads if payload.stage is not None)
            else:
                reason_counts = Counter(payload.error_code or "unknown_error" for payload in payloads)
                for code, count in sorted(reason_counts.items()):
                    lines.append(f"reason_code: {code} count={count}")
                first = payloads[0]
                if first.operation is not None:
                    lines.append("operation: " + first.operation)
                if first.error_reason is not None:
                    lines.append("reason: " + first.error_reason)
                error_class = first.error_class
                if error_class:
                    lines.append(f"exception: {error_class}")
        selected_examples = sorted(examples, key=self._payload_sort_key)[:_NOTIFICATION_EXAMPLE_LIMIT]
        for position, payload in enumerate(selected_examples, start=1):
            lines.extend(self._render_image_example(position, payload))
        if log_path:
            lines.append("Log: [REDACTED]")
        detail = "\n".join(lines)
        if len(detail) > _NOTIFICATION_BODY_LIMIT:
            detail = detail[: _NOTIFICATION_BODY_LIMIT - 1] + "…"
        for method in self._methods_for(grouped):
            await self._deliver(method, title, detail)

    @staticmethod
    def _payload_sort_key(payload: EventPayload) -> tuple[int, int, str, str]:
        return (
            int(payload.chapter_id) if payload.chapter_id is not None else -1,
            payload.image_index if payload.image_index is not None else -1,
            payload.stage or "",
            payload.url or "",
        )

    @staticmethod
    def _short_url(value: str) -> str:
        return value if len(value) <= _NOTIFICATION_URL_LIMIT else value[:_NOTIFICATION_URL_LIMIT] + "…"

    def _render_image_example(self, position: int, payload: EventPayload) -> list[str]:
        label = f"example {position}"
        if payload.chapter_id is not None:
            label += f": chapter={payload.chapter_id}"
        if payload.image_index is not None:
            label += f" image={payload.image_index}"
        lines = [label]
        if payload.url is not None:
            lines.append("image_url: " + self._short_url(payload.url))
        lines.append("stage: " + (payload.stage or "image_processing"))
        if payload.response_url is not None:
            lines.append("response_url: " + self._short_url(payload.response_url))
        if payload.http_status is not None:
            lines.append(f"http_status: {payload.http_status}")
        transport = payload.transport
        if (
            transport is None
            and payload.stage in {"image_processing", "image_save"}
            and payload.http_status is not None
        ):
            transport = "completed"
        if transport is not None:
            lines.append("transport: " + transport)
        if payload.path is not None:
            lines.append("output_path: " + payload.path)
        if payload.error_code is not None:
            lines.append("reason_code: " + payload.error_code)
        if payload.error_reason is not None:
            lines.append("reason: " + payload.error_reason)
        if payload.error_class is not None:
            lines.append("exception: " + payload.error_class)
        return lines

    def _methods_for(
        self,
        grouped: Mapping[NotificationCategory, list[EventPayload]],
    ) -> list[NotificationMethod]:
        selected: list[NotificationMethod] = []
        for category in grouped:
            configured = self.config.routes.get(category, self.config.methods)
            for method in configured:
                if method not in selected:
                    selected.append(method)
        return selected

    async def _deliver(
        self,
        channel: NotificationMethod,
        title: str,
        message: str,
    ) -> bool:
        sender = self.senders.get(channel)
        try:
            if sender is None:
                raise RuntimeError(f"notification sender is not configured: {channel}")
            result = await sender.send(title, message)
        except Exception as exc:
            await self.logger.core("notification_failed", module="notification", error=exc)
            if self.events is not None:
                await self.events.emit(
                    EventName.NOTIFICATION_FAILED,
                    EventPayload(
                        channel=channel,
                        error=str(exc),
                        error_class=type(exc).__name__,
                    ),
                )
            return False
        if result:
            await self.logger.core("notification_sent", module="notification")
            if self.events is not None:
                await self.events.emit(
                    EventName.NOTIFICATION_SENT,
                    EventPayload(channel=channel),
                )
            return True
        await self.logger.core("notification_failed", module="notification")
        if self.events is not None:
            await self.events.emit(EventName.NOTIFICATION_FAILED, EventPayload(channel=channel))
        return False


def _send_mail(settings: dict[str, Any], title: str, message: str) -> None:
    mail = EmailMessage()
    mail["Subject"] = title
    mail["From"] = settings["from"]
    mail["To"] = ", ".join(settings["to"])
    mail.set_content(message)
    context = ssl.create_default_context()
    ssl_mode = settings.get("use_tls", True) and int(settings.get("smtp_port", 465)) == 465
    client = (
        smtplib.SMTP_SSL(
            settings["smtp_host"],
            int(settings.get("smtp_port", 465)),
            timeout=30,
            context=context,
        )
        if ssl_mode
        else smtplib.SMTP(
            settings["smtp_host"],
            int(settings.get("smtp_port", 465)),
            timeout=30,
        )
    )
    with client:
        if not ssl_mode and settings.get("use_tls", True):
            client.starttls(context=context)
        if settings.get("username"):
            client.login(settings["username"], settings.get("password", ""))
        client.send_message(mail)
