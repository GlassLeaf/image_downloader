"""Authentication requires TLS before consulting credentials or opening SMTP."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from image_downloader.configuration.models import Email, Notification
from image_downloader.exceptions import ConfigurationError
from image_downloader.observability.notification_delivery import (
    EmailNotificationSender,
    _send_mail,
    validate_notification_delivery,
)


def settings(**overrides):
    return dict(
        smtp_host="mail.example.test",
        smtp_port=587,
        use_tls=False,
        credential_reference="MAIL",
        **{"from": "from@example.test", "to": ["to@example.test"]},
        **overrides,
    )


@pytest.mark.parametrize("entry", ["sender", "adapter"])
def test_insecure_auth_rejected_before_credentials_or_connection(monkeypatch, entry):
    credentials, connect = Mock(), Mock()
    monkeypatch.setattr("smtplib.SMTP", connect)
    monkeypatch.setattr("smtplib.SMTP_SSL", connect)
    with pytest.raises(ConfigurationError, match="authentication requires TLS"):
        if entry == "sender":
            asyncio.run(EmailNotificationSender(Email.model_validate(settings()), credentials).send("title", "body"))
        else:
            _send_mail(settings(), "title", "body")
    credentials.get_smtp.assert_not_called()
    connect.assert_not_called()


@pytest.mark.parametrize("reachable", [False, True])
def test_preflight_checks_only_reachable_email(reachable):
    config = Notification.model_validate(
        {"enabled": reachable, "methods": ["email"], "notify_on": ["download_success"], "email": settings()}
    )
    if reachable:
        with pytest.raises(ConfigurationError, match="authentication requires TLS"):
            validate_notification_delivery(config)
    else:
        validate_notification_delivery(config)


def test_email_excluded_by_routes_is_not_validated():
    config = Notification.model_validate({"enabled": True, "methods": ["email"], "notify_on": [], "email": settings()})
    validate_notification_delivery(config)


@pytest.mark.parametrize("port,tls,reference", [(465, True, "MAIL"), (587, True, "MAIL"), (2525, False, None)])
def test_supported_modes_and_starttls_failure(monkeypatch, port, tls, reference):
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    client.send_message.return_value = {}
    connect = Mock(return_value=client)
    monkeypatch.setattr("smtplib.SMTP", connect)
    monkeypatch.setattr("smtplib.SMTP_SSL", connect)
    config = settings()
    config.update(smtp_port=port, use_tls=tls, credential_reference=reference)
    credentials = SimpleNamespace(get_smtp=lambda _: SimpleNamespace(username="user", password="artificial"))
    asyncio.run(EmailNotificationSender(Email.model_validate(config), credentials).send("title", "body"))
    assert client.starttls.call_count == int(tls and port != 465)
    assert client.login.call_count == int(reference is not None)
    if tls and port != 465:
        client.reset_mock()
        client.starttls.side_effect = RuntimeError("TLS unavailable")
        with pytest.raises(RuntimeError, match="TLS unavailable"):
            asyncio.run(EmailNotificationSender(Email.model_validate(config), credentials).send("title", "body"))
        client.login.assert_not_called()
