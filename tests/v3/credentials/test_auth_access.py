from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from image_downloader import PluginAuthAccess, SiteAuthAccess
from image_downloader.credentials.access import CommandAuthAccess, compile_auth_access, resolve_auth_access
from image_downloader.exceptions import ConfigurationError, LoginApprovalRequiredError


@pytest.mark.parametrize(
    "values,credentials,login",
    [
        ({}, False, "deny"),
        ({"secrets": {"token": "TOKEN"}}, True, "ask"),
        ({"credential_access": "allow"}, True, "ask"),
        ({"secrets": {"token": "TOKEN"}, "credential_access": "deny"}, False, "deny"),
        ({"credential_access": "deny", "login": "allow"}, False, "allow"),
        ({"secrets": {"token": "TOKEN"}, "login": "deny", "mode": "anonymous"}, True, "deny"),
    ],
)
def test_site_defaults_keep_delivery_login_and_persistence_independent(values, credentials, login):
    site = SiteAuthAccess(origins=("https://a.test",), **values)
    assert site.provides_credentials is credentials
    assert site.login_policy == login


def test_origin_matching_is_exact_and_groups_are_user_owned():
    policy = PluginAuthAccess.model_validate(
        {
            "mode": "anonymous",
            "sites": {
                "a": {"origins": ["https://A.test", "https://login.a.test:443/"], "secrets": {"token": "A"}},
                "b": {"origins": ["http://a.test"], "mode": "standard", "secrets": {"token": "B"}},
            },
        }
    )
    assert resolve_auth_access("a.b", "https://a.test/work", policy).site.secrets == {"token": "A"}
    assert resolve_auth_access("a.b", "https://login.a.test/work", policy).site.mode == "anonymous"
    assert resolve_auth_access("a.b", "http://a.test/work", policy).site.mode == "standard"
    missing = resolve_auth_access("a.b", "https://a.test:444/work", policy)
    assert not missing.site.provides_credentials and missing.site.login_policy == "deny"
    assert missing.site.mode == "anonymous"
    overlapping = PluginAuthAccess.model_validate(
        {"sites": {"a": {"origins": ["https://a.test"]}, "b": {"origins": ["https://A.test:443/"]}}}
    )
    with pytest.raises(ConfigurationError, match="must not overlap"):
        compile_auth_access("a.b", overlapping)


@pytest.mark.parametrize("origin", ["https://*.co.jp", "https://a.test/path", "https://u:p@a.test", 1])
def test_site_origin_rejects_non_exact_or_invalid_inputs(origin):
    with pytest.raises(ValidationError):
        SiteAuthAccess(origins=[origin])


@pytest.mark.parametrize("decision", [True, False])
def test_consent_coalesces_and_lasts_only_for_one_command(decision):
    async def scenario():
        approver = AsyncMock(return_value=decision)
        access = resolve_auth_access(
            "a.b",
            "https://a.test",
            PluginAuthAccess.model_validate(
                {
                    "sites": {
                        "a": {"origins": ["https://a.test"], "login": "ask"},
                    }
                }
            ),
        )
        command = CommandAuthAccess(approver)
        assert await asyncio.gather(*(command.approve(access, "required") for _ in range(12))) == [decision] * 12
        assert approver.await_count == 1
        await CommandAuthAccess(approver).approve(access, "required")
        assert approver.await_count == 2

    asyncio.run(scenario())


def test_unattended_consent_requires_explicit_configuration(monkeypatch):
    async def scenario():
        access = resolve_auth_access(
            "a.b",
            "https://a.test",
            PluginAuthAccess.model_validate(
                {
                    "sites": {
                        "a": {"origins": ["https://a.test"], "login": "ask"},
                    }
                }
            ),
        )
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        with pytest.raises(LoginApprovalRequiredError):
            await CommandAuthAccess().approve(access, "required")

    asyncio.run(scenario())
