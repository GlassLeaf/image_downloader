"""Error-suffixed public exceptions are a deliberate incompatible API rename."""

from __future__ import annotations

import asyncio
import importlib
import tomllib
from unittest.mock import AsyncMock

import pytest

from image_downloader import (
    AppConfig,
    AuthenticationError,
    ImageDownloaderError,
    PluginError,
    RequestSpec,
    SecretNotFoundError,
    UnsupportedSiteFeatureError,
)
from image_downloader.commands.errors import _exit_status
from image_downloader.credentials import plugin_secrets
from image_downloader.exceptions import PUBLIC_EXCEPTION_NAMES, error_info_for
from image_downloader.observability.logging import safe_exception_name
from image_downloader.runtime import RequestGateway, RuntimeSecrets


@pytest.mark.parametrize("module_name", ["image_downloader", "image_downloader.exceptions"])
@pytest.mark.parametrize(
    "old,new,expected",
    [
        ("SecretNotFound", "SecretNotFoundError", SecretNotFoundError),
        ("UnsupportedSiteFeature", "UnsupportedSiteFeatureError", UnsupportedSiteFeatureError),
    ],
)
def test_new_public_names_exist_and_old_names_are_removed(module_name, old, new, expected):
    module = importlib.import_module(module_name)
    assert getattr(module, new) is expected
    assert not hasattr(module, old)
    with pytest.raises(ImportError):
        exec(f"from {module_name} import {old}", {})
    assert new in PUBLIC_EXCEPTION_NAMES and old not in PUBLIC_EXCEPTION_NAMES


@pytest.mark.parametrize(
    "exception,parent,code,reason",
    [
        (SecretNotFoundError, AuthenticationError, "secret_not_found", "required secret was not found"),
        (UnsupportedSiteFeatureError, PluginError, "unsupported_site_feature", "site requires an unsupported feature"),
    ],
)
def test_parent_catches_and_catalog_codes_remain_stable(exception, parent, code, reason):
    error = exception("fictional-private-value")
    with pytest.raises(parent) as caught:
        raise error
    assert caught.value is error and isinstance(error, ImageDownloaderError)
    info = error_info_for(error)
    assert info.code == code and info.reason == reason and info.exception == exception.__name__
    assert "fictional-private-value" not in repr(info)
    assert safe_exception_name(exception.__name__) == exception.__name__


def test_actual_missing_secret_uses_new_type_and_existing_message(monkeypatch):
    monkeypatch.setattr(plugin_secrets.os, "getenv", lambda name: None)
    monkeypatch.setattr(plugin_secrets, "load_secret", lambda service, name: None)
    with pytest.raises(SecretNotFoundError, match="required plugin secret is unavailable") as caught:
        RuntimeSecrets("com.example.plugin", {"token": "TOKEN"}).get("token")
    assert type(caught.value) is SecretNotFoundError
    assert error_info_for(caught.value).exception == "SecretNotFoundError"


@pytest.mark.parametrize(
    "spec",
    [
        RequestSpec("ftp://example.test/file"),
        RequestSpec("https://example.test/", headers={"Accept": "text/event-stream"}),
    ],
)
def test_actual_gateway_rejection_uses_new_type_without_network(spec, monkeypatch):
    async def scenario():
        gateway = RequestGateway(AppConfig())
        monkeypatch.setattr(gateway.client, "send", AsyncMock(side_effect=AssertionError("network must not start")))
        try:
            with pytest.raises(UnsupportedSiteFeatureError) as caught:
                operation = gateway.operation(plugin_id=None, operation_url="https://example.test/")
                await operation.execute(spec)
            assert type(caught.value) is UnsupportedSiteFeatureError
            assert error_info_for(caught.value).exception == "UnsupportedSiteFeatureError"
        finally:
            await gateway.close()

    asyncio.run(scenario())


def test_n818_is_a_normal_quality_gate(repository_root):
    config = tomllib.loads((repository_root / "pyproject.toml").read_text(encoding="utf-8"))
    assert "N818" in config["tool"]["ruff"]["lint"]["select"]


@pytest.mark.parametrize("exception,status", [(SecretNotFoundError, 3), (UnsupportedSiteFeatureError, 4)])
def test_cli_exit_status_is_unchanged(exception, status):
    assert _exit_status(exception()) == status
