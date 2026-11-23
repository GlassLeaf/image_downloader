from __future__ import annotations

import asyncio

import httpx
import pytest
import yaml
from support.plugins import make_plugin
from support.vault import FixedKeys

from image_downloader import AuthLease, AuthStatus, AuthVerification
from image_downloader.application.site_operation import SiteOperationFactory
from image_downloader.config import AppConfig
from image_downloader.configuration.models import PluginConnections
from image_downloader.credentials.vault import CredentialVault
from image_downloader.exceptions import ConfigurationError, error_info_for
from image_downloader.plugins.plugin_invoker import PluginInvoker
from image_downloader.plugins.runtime import PluginRuntime
from image_downloader.transport.gateway import RequestGateway


def test_operation_uses_final_config_and_replaces_author_origin_arrays(tmp_path):
    directory = make_plugin(tmp_path)
    (directory / "entry.yaml").write_text(
        yaml.safe_dump(
            {
                "config": {"author": True, "hosts": ["author.test"]},
                "endpoints": {
                    "images": {
                        "origin": "https://cdn.${config.hosts}",
                        "auth": "none",
                        "verification": {"before_request": True},
                    },
                    "fixed": {"origin": ["https://one.test", "https://two.test"], "auth": "none"},
                },
            }
        ),
        encoding="utf-8",
    )
    config = AppConfig.model_validate(
        {
            "plugin_settings": {
                "com.example.gallery": {
                    "config": {"hosts": ["profile.test"]},
                    "endpoints": {"fixed": {"origin": ["https://replacement.test"]}},
                }
            }
        }
    )
    runtime = PluginRuntime(config, tmp_path, mode="off")

    async def scenario():
        gateway = RequestGateway(
            config,
            transport_factory=lambda: httpx.MockTransport(
                lambda request: pytest.fail("building an operation must not send")
            ),
        )
        credentials = CredentialVault(tmp_path / "data", keys=FixedKeys()).profile("default")
        factory = SiteOperationFactory(config, runtime, gateway, None, credentials)
        overrides = {"com.example.gallery": {"hosts": ["cli.test"]}}
        try:
            runtime.doctor_validate(overrides)
            assert all(item.loaded for item in runtime.diagnostics)
            record, plugin = factory.select("https://example.test/page", overrides, None, None, False)
            context, session, _ = factory.build(
                "https://example.test/page",
                record,
                plugin,
                PluginInvoker(record.id),
                overrides,
                None,
            )
            assert context.config["hosts"] == ("cli.test",)
            assert session.auth.context.config == context.config
            assert context.endpoints["images"].origin == "https://cdn.cli.test:443"
            assert context.endpoints["fixed"].origins == ("https://replacement.test:443",)
            assert session.auth.lookup("https://cdn.cli.test:443").policy.verify_before_request is True
        finally:
            await gateway.close()

    try:
        asyncio.run(scenario())
    finally:
        runtime.close()


def test_author_connections_merge_partial_profile_overrides_without_entering_private_config(tmp_path):
    directory = make_plugin(tmp_path)
    (directory / "entry.yaml").write_text(
        yaml.safe_dump(
            {
                "config": {"author": True},
                "authentication": {"verify_before_request": True, "verify_interval_seconds": 12},
                "endpoints": {
                    "api": {"origin": "https://api.test", "auth": "session", "verification": {"before_request": False}}
                },
            }
        ),
        encoding="utf-8",
    )
    config = AppConfig.model_validate(
        {
            "plugin_settings": {
                "com.example.gallery": {
                    "authentication": {"verify_on_start": True},
                    "endpoints": {"api": {"origin": "https://custom.test", "verification": {"interval_seconds": None}}},
                }
            }
        }
    )
    runtime = PluginRuntime(config, tmp_path, mode="off")
    try:
        record = runtime.records["com.example.gallery"]
        assert runtime.effective_config(record) == {"author": True}
        connections = runtime.effective_connections(record)
        assert connections.authentication.verify_before_request is True
        assert connections.authentication.verify_on_start is True
        assert connections.authentication.verify_interval_seconds == 12
        assert connections.endpoints["api"].origin == "https://custom.test:443"
        assert connections.endpoints["api"].auth == "session"
        assert connections.endpoints["api"].verification.model_dump(exclude_unset=True) == {
            "before_request": False,
            "interval_seconds": None,
        }
    finally:
        runtime.close()


@pytest.mark.parametrize("origin", ["https://*.test", "https://host.test/path", "https://user:pass@host.test"])
def test_invalid_origin_is_rejected(origin):
    with pytest.raises(ValueError):
        PluginConnections.model_validate({"endpoints": {"bad": {"origin": origin, "auth": "session"}}})


def test_conflicting_aliases_are_detected_after_origin_normalization():
    with pytest.raises(ValueError, match="conflicting authentication"):
        PluginConnections.model_validate(
            {
                "endpoints": {
                    "one": {"origin": "https://example.test", "auth": "session"},
                    "two": {"origin": "https://EXAMPLE.test:443/", "auth": "none"},
                }
            }
        )


def test_incomplete_merged_declaration_is_configuration_error(tmp_path):
    make_plugin(tmp_path)
    config = AppConfig.model_validate(
        {
            "plugin_settings": {
                "com.example.gallery": {
                    "endpoints": {"api": {"origin": "https://api.test"}},
                }
            }
        }
    )
    runtime = PluginRuntime(config, tmp_path, mode="off")
    try:
        with pytest.raises(ConfigurationError):
            runtime.effective_connections(runtime.records["com.example.gallery"])
    finally:
        runtime.close()


@pytest.mark.parametrize("value", [True, -1, 0, float("inf"), float("nan"), 10**1000])
def test_lease_requires_a_positive_finite_duration(value):
    with pytest.raises(ValueError):
        AuthLease(value)


def test_only_valid_verification_can_replace_a_lease():
    with pytest.raises(ValueError):
        AuthVerification(AuthStatus.UNKNOWN, AuthLease(10))


def test_new_authentication_errors_keep_public_diagnostic_codes():
    from image_downloader import AuthReentryError, AuthScopeError, AuthStateUnknownError

    for error in (AuthReentryError(), AuthScopeError(), AuthStateUnknownError()):
        info = error_info_for(error)
        assert info.code == error.code
        assert info.exception == type(error).__name__
