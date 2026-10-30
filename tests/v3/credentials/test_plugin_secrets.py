from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from pathlib import Path
from unittest.mock import AsyncMock
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import ValidationError
from support.vault import MemoryKeys

from image_downloader import AppConfig, DownloadRequest, RuntimeComposer, SecretNotFoundError
from image_downloader.credentials import CredentialVault
from image_downloader.credentials.keyring import MasterKeyStore
from image_downloader.runtime import RuntimeSecrets
from image_downloader.security import install_plugin
from image_downloader.storage.cookies import CookieStore

PREFIX = "IMAGE_DOWNLOADER_PLUGIN_"


@pytest.fixture(autouse=True)
def artificial_keys(monkeypatch):
    keys = MemoryKeys()
    monkeypatch.setattr(MasterKeyStore, "get", lambda self, v, k: keys.get(v, k))
    monkeypatch.setattr(MasterKeyStore, "create", lambda self, v, k, value: keys.create(v, k, value))
    return keys


@pytest.mark.parametrize("plugin_id", ["ab", "612e62", "a-b"])
def test_id_validation_still_requires_a_dot(plugin_id):
    with pytest.raises(ValidationError):
        AppConfig.model_validate({"plugin_settings": {plugin_id: {"secrets": {"secret": "TOKEN"}}}})


@pytest.mark.parametrize("references", [{}, {"secret": ""}, {"other": "TOKEN"}])
def test_unconfigured_secret_fails_before_any_lookup(tmp_path, monkeypatch, references):
    store = CredentialVault(tmp_path).profile("default")
    monkeypatch.setattr(store, "get_plugin", lambda *args: pytest.fail("unexpected lookup"))
    with pytest.raises(SecretNotFoundError, match="not configured"):
        RuntimeSecrets("a.b", references, store=store).get("secret")


def test_lookup_is_lazy_mapping_detached_and_ids_do_not_collide(tmp_path, monkeypatch):
    store = CredentialVault(tmp_path).profile("default")
    references = {"first": "TOKEN", "second": "TOKEN"}
    provider = RuntimeSecrets("com.example.a-b", references, store=store)
    references["first"] = "OTHER"
    monkeypatch.setenv(PREFIX + "COM_EXAMPLE_A_B_TOKEN", "unused-canary")
    with pytest.raises(SecretNotFoundError, match="unavailable"):
        provider.get("first")
    store.set_plugin("com.example.a-b", "TOKEN", "hyphen-canary")
    store.set_plugin("com.example.a.b", "TOKEN", "dot-canary")
    assert provider.get("first") == provider.get("second") == "hyphen-canary"
    assert RuntimeSecrets("com.example.a.b", {"secret": "TOKEN"}, store=store).get("secret") == "dot-canary"
    store.set_plugin("com.example.a-b", "TOKEN", "updated-canary")
    assert provider.get("first") == "updated-canary"


UNIT_HEX = {
    "cursor-api-gallery": "6C6F63616C2E696D6167652D646F776E6C6F616465722E637572736F722D6170692D67616C6C657279",
    "oauth-media-api": "6C6F63616C2E696D6167652D646F776E6C6F616465722E6F617574682D6D656469612D617069",
    "csrf-login-gallery": "6C6F63616C2E696D6167652D646F776E6C6F616465722E637372662D6C6F67696E2D67616C6C657279",
}
UNIT_HOST = {
    "cursor-api-gallery": "cursor-api",
    "oauth-media-api": "oauth-media",
    "csrf-login-gallery": "csrf-login",
}
UNIT_SECRETS = {
    "cursor-api-gallery": [("api_token", "TOKEN", "544F4B454E", "cursor-canary")],
    "oauth-media-api": [
        ("access_token", "TOKEN", "544F4B454E", "access-canary"),
        ("refresh_token", "REFRESH", "52454652455348", "refresh-canary"),
    ],
    "csrf-login-gallery": [
        ("username", "USERNAME", "555345524E414D45", "user-canary"),
        ("password", "PASSWORD", "50415353574F5244", "password-canary"),
    ],
}


def _compose(tmp_path, plugin_sources, unit):
    root = tmp_path / "plugins"
    install_plugin(root, plugin_sources / unit)
    plugin_id = "local.image-downloader." + unit
    config = AppConfig.model_validate(
        {
            "storage": {"data_root": str(tmp_path / "data")},
            "plugins": {"root": str(root)},
            "logging": {"console": {"enabled": False}},
            "network": {"max_attempts": 1},
            "download": {"allow_empty_chapter_manifest": True},
            "security": {"plugin_verification": "strict"},
            "plugin_settings": {plugin_id: {"secrets": {name: ref for name, ref, _, _ in UNIT_SECRETS[unit]}}},
        }
    )
    service = RuntimeComposer(config, config_root=tmp_path, plugin_root=root).compose()
    service._persist_cookies = AsyncMock()
    return service


def _mock_transport(monkeypatch):
    client_class = httpx.AsyncClient
    observed = []
    calls = {}

    def respond(request):
        observed.append(request)
        host, path = request.url.host, request.url.path
        calls[host, path] = calls.get((host, path), 0) + 1
        if host == "cursor-api.example.test":
            assert request.headers["x-api-key"] == "cursor-canary"
            return httpx.Response(200, json={"gallery": {"id": "1", "title": "Cursor", "images": []}})
        if host == "oauth-media.example.test":
            if path == "/oauth/token":
                assert parse_qs(request.content.decode())["refresh_token"] == ["refresh-canary"]
                return httpx.Response(200, json={"access_token": "renewed-canary"})
            expected = "access-canary" if calls[host, path] == 1 else "renewed-canary"
            assert request.headers["authorization"] == "Bearer " + expected
            if calls[host, path] == 1:
                return httpx.Response(200, json={"error": "expired_token"})
            return httpx.Response(200, json={"media": {"id": "1", "title": "OAuth", "images": []}})
        assert host == "csrf-login.example.test"
        if path == "/login":
            return httpx.Response(200, text='<input name="csrf" value="csrf-canary">')
        if path == "/session":
            assert parse_qs(request.content.decode()) == {
                "username": ["user-canary"],
                "password": ["password-canary"],
                "csrf": ["csrf-canary"],
            }
            return httpx.Response(200)
        return httpx.Response(200, text="login-required" if calls[host, path] == 1 else "<title>CSRF</title>")

    def client(*args, **kwargs):
        return client_class(*args, **dict(kwargs, transport=httpx.MockTransport(respond)))

    monkeypatch.setattr(httpx, "AsyncClient", client)
    monkeypatch.setattr(CookieStore, "load", lambda _: [])
    return observed


@pytest.mark.parametrize(
    "units", [("cursor-api-gallery",), ("oauth-media-api",), ("csrf-login-gallery",), tuple(UNIT_HEX)]
)
def test_signed_auth_plugins_resolve_secrets_in_runtime_contexts_and_multiple_runtimes(
    tmp_path: Path, plugin_sources: Path, monkeypatch, units
):
    observed = _mock_transport(monkeypatch)
    canaries = ["unused-legacy-canary", "renewed-canary", "csrf-canary"]
    for unit in units:
        for _, _, ref_hex, value in UNIT_SECRETS[unit]:
            canaries.append(value)
            monkeypatch.setenv(PREFIX + UNIT_HEX[unit] + "_" + ref_hex, "unused-legacy-canary")

    async def scenario():
        async with AsyncExitStack() as stack:
            services = []
            for unit in units:
                unit_root = tmp_path / unit
                unit_root.mkdir()
                store = CredentialVault(unit_root / "data").profile("default")
                for _, reference, _, value in UNIT_SECRETS[unit]:
                    store.set_plugin("local.image-downloader." + unit, reference, value)
                services.append(await stack.enter_async_context(_compose(unit_root, plugin_sources, unit)))
            results = await asyncio.gather(
                *(
                    service.download(DownloadRequest(url=f"https://{UNIT_HOST[unit]}.example.test/works/1"))
                    for unit, service in zip(units, services, strict=True)
                )
            )
            assert [item.manifest.title for item in results] == [
                {"cursor-api-gallery": "Cursor", "oauth-media-api": "OAuth", "csrf-login-gallery": "CSRF"}[unit]
                for unit in units
            ]
            assert all(item.manifest.chapters[0].images == () and not item.failures for item in results)

    asyncio.run(scenario())
    assert len(observed) == sum(
        {"cursor-api-gallery": 1, "oauth-media-api": 3, "csrf-login-gallery": 4}[u] for u in units
    )
    logs = "\n".join(path.read_text(encoding="utf-8") for path in tmp_path.rglob("*.log"))
    assert logs.count("operation_started") == len(units)
    assert all(value not in logs for value in canaries)
    assert all(reference not in logs for unit in units for _, reference, _, _ in UNIT_SECRETS[unit])
