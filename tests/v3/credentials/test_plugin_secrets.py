from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from pathlib import Path
from unittest.mock import AsyncMock
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import ValidationError

from image_downloader import AppConfig, RuntimeComposer, SecretNotFound
from image_downloader.credentials import plugin_secrets
from image_downloader.runtime import RuntimeSecrets
from image_downloader.security import install_plugin
from image_downloader.storage.cookies import CookieStore

PREFIX = "IMAGE_DOWNLOADER_PLUGIN_"
AB = PREFIX + "636F6D2E6578616D706C652E612D62_544F4B454E"
DOT = PREFIX + "636F6D2E6578616D706C652E612E62_544F4B454E"
REFERENCE_BOUNDARY = PREFIX + "636F6D2E6578616D706C652E61_425F544F4B454E"
SIMPLE = PREFIX + "612E62_544F4B454E"
LEGACY_COLLISION = PREFIX + "COM_EXAMPLE_A_B_TOKEN"


@pytest.fixture(autouse=True)
def no_real_keyring(monkeypatch):
    def unexpected(service, username):
        pytest.fail("unexpected credential-store access")

    monkeypatch.setattr(plugin_secrets, "load_secret", unexpected)


@pytest.mark.parametrize(
    ("plugin_id", "reference", "suffix"),
    [
        ("a.b", "TOKEN", "612E62_544F4B454E"),
        ("com.example.a-b", "TOKEN", "636F6D2E6578616D706C652E612D62_544F4B454E"),
        ("com.example.a.b", "TOKEN", "636F6D2E6578616D706C652E612E62_544F4B454E"),
        ("com.example.a", "B_TOKEN", "636F6D2E6578616D706C652E61_425F544F4B454E"),
        ("a.B", "token", "612E42_746F6B656E"),
        ("é.x", "鍵", "C3A92E78_E98DB5"),
        ("\ud800.x", "\udfff", "EDA0802E78_EDBFBF"),
        ("a.b", "_0_TOKEN_", "612E62_5F305F544F4B454E5F"),
    ],
)
def test_secret_lookup_uses_fixed_permanent_environment_names(monkeypatch, plugin_id, reference, suffix):
    requested = []

    def getenv(name):
        requested.append(name)
        return "canary-value"

    monkeypatch.setattr(plugin_secrets.os, "getenv", getenv)
    assert RuntimeSecrets(plugin_id, {"secret": reference}).get("secret") == "canary-value"
    assert requested == [PREFIX + suffix]
    assert suffix.count("_") == 1 and suffix.isascii() and suffix == suffix.upper()


@pytest.mark.parametrize(
    ("plugin_id", "reference", "expected"),
    [
        ("com.example.a-b", "TOKEN", AB),
        ("com.example.a.b", "TOKEN", DOT),
        ("com.example.a", "B_TOKEN", REFERENCE_BOUNDARY),
    ],
)
def test_previously_colliding_pairs_only_receive_their_own_secret(monkeypatch, plugin_id, reference, expected):
    monkeypatch.setenv(LEGACY_COLLISION, "legacy-canary")
    for index, name in enumerate((AB, DOT, REFERENCE_BOUNDARY)):
        monkeypatch.setenv(name, f"owner-{index}")
    config = AppConfig.model_validate({"plugin_settings": {plugin_id: {"secrets": {"secret": reference}}}})
    provider = RuntimeSecrets(plugin_id, config.plugin_settings[plugin_id].secrets)
    assert provider.get("secret") == f"owner-{(AB, DOT, REFERENCE_BOUNDARY).index(expected)}"
    assert len({AB.upper(), DOT.upper(), REFERENCE_BOUNDARY.upper()}) == 3
    assert plugin_secrets.os.getenv(LEGACY_COLLISION) == "legacy-canary"


@pytest.mark.parametrize(
    ("plugin_id", "reference"),
    [("a.b", "TOKEN"), ("com.example.a-b", "TOKEN"), ("com.example.a.b", "TOKEN"), ("a--b.c", "_0_TOKEN_")],
)
def test_valid_legacy_names_and_encoded_names_are_disjoint(plugin_id, reference):
    AppConfig.model_validate({"plugin_settings": {plugin_id: {"secrets": {"secret": reference}}}})
    # Freeze the former grammar rather than using the new encoder to generate old names.
    legacy_suffix = plugin_id.upper().replace(".", "_").replace("-", "_") + "_" + reference
    assert legacy_suffix.count("_") >= 2
    assert RuntimeSecrets._environment_name(plugin_id, reference).removeprefix(PREFIX).count("_") == 1


@pytest.mark.parametrize("plugin_id", ["ab", "612e62", "a-b"])
def test_id_validation_still_requires_a_dot(plugin_id):
    with pytest.raises(ValidationError):
        AppConfig.model_validate({"plugin_settings": {plugin_id: {"secrets": {"secret": "TOKEN"}}}})


@pytest.mark.parametrize("environment_value", ["env-canary", "0", " "])
def test_nonempty_environment_values_win_without_keyring_access(monkeypatch, environment_value):
    monkeypatch.setenv(SIMPLE, environment_value)
    assert RuntimeSecrets("a.b", {"secret": "TOKEN"}).get("secret") == environment_value


@pytest.mark.parametrize("environment_value", [None, ""])
@pytest.mark.parametrize("keyring_value", ["keyring-canary", ""])
def test_absent_or_empty_environment_uses_the_unchanged_keyring_identity(monkeypatch, environment_value, keyring_value):
    if environment_value is None:
        monkeypatch.delenv(SIMPLE, raising=False)
    else:
        monkeypatch.setenv(SIMPLE, environment_value)
    monkeypatch.setenv(PREFIX + "A_B_TOKEN", "unused-legacy-canary")
    calls = []

    def keyring(service, username):
        calls.append((service, username))
        return keyring_value

    monkeypatch.setattr(plugin_secrets, "load_secret", keyring)
    assert RuntimeSecrets("a.b", {"secret": "TOKEN"}).get("secret") == keyring_value
    assert calls == [("image-downloader.plugin.a.b", "TOKEN")]


@pytest.mark.parametrize("references", [{}, {"secret": ""}, {"other": "TOKEN"}])
def test_unconfigured_secret_fails_before_any_lookup(monkeypatch, references):
    monkeypatch.setattr(plugin_secrets.os, "getenv", lambda _: pytest.fail("unexpected environment access"))
    with pytest.raises(SecretNotFound) as error:
        RuntimeSecrets("a.b", references).get("secret")
    assert str(error.value) == "required plugin secret is not configured"


@pytest.mark.parametrize("environment_value", [None, ""])
def test_unavailable_secret_ignores_legacy_values_and_keeps_the_safe_error(monkeypatch, environment_value, capsys):
    if environment_value is None:
        monkeypatch.delenv(SIMPLE, raising=False)
    else:
        monkeypatch.setenv(SIMPLE, environment_value)
    monkeypatch.setenv(PREFIX + "A_B_TOKEN", "unused-legacy-canary")
    monkeypatch.setattr(plugin_secrets, "load_secret", lambda _service, _username: None)
    with pytest.raises(SecretNotFound) as error:
        RuntimeSecrets("a.b", {"secret": "TOKEN"}).get("secret")
    assert str(error.value) == "required plugin secret is unavailable"
    assert capsys.readouterr() == ("", "")


def test_environment_is_read_lazily_and_the_reference_mapping_is_detached(monkeypatch):
    references = {"first": "TOKEN", "second": "TOKEN"}
    provider = RuntimeSecrets("a.b", references)
    references["first"] = "OTHER"
    monkeypatch.setenv(SIMPLE, "first-canary")
    assert provider.get("first") == provider.get("second") == "first-canary"
    monkeypatch.setenv(SIMPLE, "second-canary")
    assert provider.get("first") == provider.get("second") == "second-canary"


def test_colliding_ids_keep_distinct_keyring_services(monkeypatch):
    for name in (AB, DOT):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(LEGACY_COLLISION, "unused-legacy-canary")
    calls = []
    values = {
        ("image-downloader.plugin.com.example.a-b", "TOKEN"): "hyphen-canary",
        ("image-downloader.plugin.com.example.a.b", "TOKEN"): "dot-canary",
    }

    def keyring(service, username):
        calls.append((service, username))
        return values[service, username]

    monkeypatch.setattr(plugin_secrets, "load_secret", keyring)
    assert RuntimeSecrets("com.example.a-b", {"secret": "TOKEN"}).get("secret") == "hyphen-canary"
    assert RuntimeSecrets("com.example.a.b", {"secret": "TOKEN"}).get("secret") == "dot-canary"
    assert calls == list(values)


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


@pytest.mark.parametrize("source", ["environment", "keyring"])
@pytest.mark.parametrize(
    "units", [("cursor-api-gallery",), ("oauth-media-api",), ("csrf-login-gallery",), tuple(UNIT_HEX)]
)
def test_signed_auth_plugins_resolve_secrets_in_runtime_contexts_and_multiple_runtimes(
    tmp_path: Path, plugin_sources: Path, monkeypatch, source, units
):
    observed = _mock_transport(monkeypatch)
    keyring_values = {}
    keyring_calls = []
    canaries = ["unused-legacy-canary", "renewed-canary", "csrf-canary"]
    for unit in units:
        plugin_id = "local.image-downloader." + unit
        for _, reference, ref_hex, value in UNIT_SECRETS[unit]:
            key = PREFIX + UNIT_HEX[unit] + "_" + ref_hex
            canaries.append(value)
            monkeypatch.setenv(
                PREFIX + plugin_id.upper().replace(".", "_").replace("-", "_") + "_" + reference, "unused-legacy-canary"
            )
            if source == "environment":
                monkeypatch.setenv(key, value)
            else:
                monkeypatch.delenv(key, raising=False)
                keyring_values["image-downloader.plugin." + plugin_id, reference] = value

    def keyring(service, username):
        keyring_calls.append((service, username))
        assert source == "keyring"
        return keyring_values[service, username]

    monkeypatch.setattr(plugin_secrets, "load_secret", keyring)

    async def scenario():
        async with AsyncExitStack() as stack:
            services = []
            for unit in units:
                unit_root = tmp_path / unit
                unit_root.mkdir()
                services.append(await stack.enter_async_context(_compose(unit_root, plugin_sources, unit)))
            results = await asyncio.gather(
                *(
                    service.run(f"https://{UNIT_HOST[unit]}.example.test/works/1")
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
    assert set(keyring_calls) == set(keyring_values)
    logs = "\n".join(path.read_text(encoding="utf-8") for path in tmp_path.rglob("*.log"))
    assert logs.count("operation_started") == len(units)
    assert all(value not in logs for value in canaries)
    assert all(reference not in logs for unit in units for _, reference, _, _ in UNIT_SECRETS[unit])
