"""Desktop and SMTP adapters and safe delivery diagnostics."""

from __future__ import annotations

import asyncio
import importlib.util
import os
import re
import smtplib
import ssl
from collections.abc import Mapping
from email.errors import HeaderParseError
from email.headerregistry import Address
from email.message import EmailMessage
from ipaddress import ip_address
from typing import Any, Protocol

from ..configuration.models import Email, Notification, NotificationMethod
from ..credentials.keyring import load_secret
from ..exceptions import ConfigurationError


def _normalize_diagnostic_host(value: str) -> str:
    """Retain only a DNS name or IP address, never arbitrary diagnostic text."""
    if not value or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value):
        return "unknown"
    literal = value.startswith("[") and value.endswith("]")
    host = value[1:-1] if literal else value
    ipv6_literal = host.lower().startswith("ipv6:")
    if ipv6_literal:
        host = host[5:]
    if "%" in host:
        return "unknown"
    try:
        address = ip_address(host)
        return address.compressed if not ipv6_literal or address.version == 6 else "unknown"
    except ValueError:
        if literal or ipv6_literal:
            return "unknown"
    try:
        host = host.encode("idna").decode("ascii").lower().removesuffix(".")
    except UnicodeError:
        return "unknown"
    labels = host.split(".")
    if len(host) > 253 or any(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) is None for label in labels):
        return "unknown"
    return host


def _smtp_diagnostic_host(value: str) -> str:
    if value.startswith("["):
        match = re.fullmatch(r"(\[[^\[\]]+\])(?::([0-9]{1,5}))?", value)
        if match is None or (match[2] is not None and int(match[2]) > 65535):
            return "unknown"
        value = match[1]
    elif value.count(":") == 1:
        host, _, port = value.partition(":")
        if re.fullmatch(r"[0-9]{1,5}", port) is None or int(port) > 65535:
            return "unknown"
        value = host
    return _normalize_diagnostic_host(value)


def _refused_domains(recipients: Mapping[str, object]) -> tuple[str, ...]:
    domains: set[str] = set()
    for recipient in recipients:
        try:
            if any(ord(character) < 32 or ord(character) == 127 for character in recipient):
                raise ValueError("invalid diagnostic address")
            domain = Address(addr_spec=recipient).domain
        except (HeaderParseError, ValueError, IndexError, RecursionError):
            domain = ""
        domains.add(_normalize_diagnostic_host(domain))
    return tuple(sorted(domains))


class _PartialRecipientRefusalError(RuntimeError):
    def __init__(self, recipients: Mapping[str, object]) -> None:
        super().__init__("some SMTP recipients were refused")
        self.refused_count = len(recipients)
        self.refused_domains = _refused_domains(recipients)


def _smtp_refusal_diagnostic(error: Exception) -> tuple[str, int, tuple[str, ...]] | None:
    if isinstance(error, _PartialRecipientRefusalError):
        return "smtp_partial_recipients_refused", error.refused_count, error.refused_domains
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        return "smtp_all_recipients_refused", len(error.recipients), _refused_domains(error.recipients)
    return None


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
        refused = client.send_message(mail)
    if refused:
        raise _PartialRecipientRefusalError(refused)
