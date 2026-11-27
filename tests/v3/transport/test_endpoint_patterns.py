from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx
import pytest
from support.auth import approved_operation

from image_downloader import (
    AuthContext,
    AuthLease,
    AuthMode,
    AuthStatus,
    AuthVerification,
    BaseAuthFlow,
    ImageResource,
    PluginExecutionContext,
    RequestSpec,
)
from image_downloader.config import AppConfig, PluginSettings
from image_downloader.exceptions import AuthScopeError, ConfigurationError
from image_downloader.transport.gateway import RequestGateway

AUTHORIZED_ORIGINS = (
    "https://cdn.host1.com",
    "https://cdn.host2.com",
    "https://a.images.example.com",
    "https://cdn.example.com",
    "https://other.example.com",
    "https://login.example.com",
    "https://public.example.com",
)


class Flow(BaseAuthFlow):
    def __init__(self):
        self.initializations = self.verifications = self.applications = 0

    async def initialize(self, context: AuthContext) -> AuthLease:
        self.initializations += 1
        return AuthLease()

    async def verify(self, context: AuthContext) -> AuthVerification:
        self.verifications += 1
        return AuthVerification(AuthStatus.VALID)

    def apply(self, request: RequestSpec) -> RequestSpec:
        self.applications += 1
        return replace(request, headers={**request.headers, "Authorization": "Bearer test-token"})


def gateway(handler):
    return RequestGateway(AppConfig(), transport_factory=lambda: httpx.MockTransport(handler))


def test_operation_scopes_are_compiled_before_auth_factory_and_frozen_per_operation():
    async def scenario():
        shared = gateway(lambda request: httpx.Response(200))
        config = {"label": "cdn"}
        settings = PluginSettings.model_validate(
            {"endpoints": {"images": {"origin": "https://${config.label}.${operation.site_domain}", "auth": "session"}}}
        )
        seen = []

        def factory(session):
            seen.append(session.endpoints["images"].origin)
            assert session.auth.context.config["label"] == "cdn"
            return Flow()

        try:
            first = approved_operation(
                shared,
                origins=AUTHORIZED_ORIGINS,
                plugin_id="test.one",
                operation_url="https://www.host1.com/page",
                settings=settings,
                plugin_config=config,
                auth_flow_factory=factory,
            )
            second = approved_operation(
                shared,
                origins=AUTHORIZED_ORIGINS,
                plugin_id="test.two",
                operation_url="https://www.host2.com/page",
                settings=settings,
                plugin_config=config,
                auth_flow_factory=factory,
            )
            config["label"] = "changed"
            assert seen == ["https://cdn.host1.com:443", "https://cdn.host2.com:443"]
            assert first.auth.context.config["label"] == "cdn"
            await first.execute(RequestSpec("https://cdn.host1.com/image"))
            await second.execute(RequestSpec("https://cdn.host2.com/image"))
            with pytest.raises(AuthScopeError):
                await first.execute(RequestSpec("https://cdn.host2.com/image"))
            with pytest.raises(AuthScopeError):
                await second.execute(RequestSpec("https://cdn.host1.com/image"))
        finally:
            await shared.close()

    asyncio.run(scenario())


def test_config_defaults_and_explicit_config_override_expand_all_hosts():
    async def scenario():
        shared = gateway(lambda request: httpx.Response(200))
        settings = PluginSettings.model_validate(
            {
                "config": {"hosts": ["author.example.com"]},
                "endpoints": {"images": {"origin": "https://cdn.${config.hosts}", "auth": "session"}},
            }
        )
        try:
            default = approved_operation(
                shared,
                origins=AUTHORIZED_ORIGINS,
                plugin_id=None,
                operation_url="https://example.com",
                settings=settings,
                auth_flow_factory=lambda _: Flow(),
            )
            override = approved_operation(
                shared,
                origins=AUTHORIZED_ORIGINS,
                plugin_id=None,
                operation_url="https://example.com",
                settings=settings,
                plugin_config={"hosts": ["host1.com", "host2.com"]},
                auth_flow_factory=lambda _: Flow(),
            )
            assert default.endpoints["images"].origin == "https://cdn.author.example.com:443"
            assert override.endpoints["images"].origins == ("https://cdn.host1.com:443", "https://cdn.host2.com:443")
            await override.execute(RequestSpec("https://cdn.host1.com/image"))
            await override.execute(RequestSpec("https://cdn.host2.com/image"))
            assert override.auth.flow.initializations == 1
            with pytest.raises(ConfigurationError, match="undefined endpoint variable"):
                approved_operation(
                    shared,
                    origins=AUTHORIZED_ORIGINS,
                    plugin_id=None,
                    operation_url="https://example.com",
                    settings=settings,
                    plugin_config={},
                    auth_flow_factory=lambda _: Flow(),
                )
        finally:
            await shared.close()

    asyncio.run(scenario())


def test_wildcard_and_exact_none_apply_consistently_to_execute_preview_and_images():
    async def scenario():
        sent = []
        shared = gateway(
            lambda request: (
                sent.append((request.url.host, request.headers.get("Authorization")))
                or httpx.Response(200, content=b"image")
            )
        )
        flow = Flow()
        settings = PluginSettings.model_validate(
            {
                "endpoints": {
                    "images": {
                        "origin": "https://*.images.${operation.site_domain}",
                        "auth": "session",
                        "verification": {"before_request": True},
                    },
                    "public": {"origin": "https://public.images.${operation.site_domain}", "auth": "none"},
                },
            }
        )

        class Plugin:
            async def create_image_request(self, image, context):
                return RequestSpec(image.url)

        try:
            session = approved_operation(
                shared,
                origins=AUTHORIZED_ORIGINS,
                plugin_id=None,
                operation_url="https://www.example.com/page",
                settings=settings,
                auth_flow_factory=lambda _: flow,
            )
            context = PluginExecutionContext({}, {}, {}, None, session, endpoints=session.endpoints)
            for host, token in (("a.images.example.com", "Bearer test-token"), ("public.images.example.com", None)):
                url = f"https://{host}/image"
                preview = await session.preview(RequestSpec(url))
                authorization = next(
                    (header.value for header in preview.headers if header.name.lower() == "authorization"), None
                )
                assert authorization == token
                await session.execute(RequestSpec(url))
                await session.execute_image(Plugin(), ImageResource(url), context)
            assert (
                sent == [("a.images.example.com", "Bearer test-token")] * 2 + [("public.images.example.com", None)] * 2
            )
            assert (flow.initializations, flow.verifications, flow.applications) == (1, 3, 3)
            with pytest.raises(AuthScopeError):
                await session.preview(RequestSpec("https://public.images.example.com/image", auth=AuthMode.REQUIRED))
            with pytest.raises(AuthScopeError):
                await session.execute(RequestSpec("https://a.b.images.example.com/image"))
            await session.execute(RequestSpec("https://unlisted.example.org/image", auth=AuthMode.NONE))
        finally:
            await shared.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "target",
    [
        "https://public.example.com/image",
        "https://nested.cdn.example.com/image",
        "https://cdn.example.com.evil.org/image",
        "https://cdn.example.com:8443/image",
        "http://cdn.example.com/image",
    ],
)
def test_authenticated_redirect_rejects_exceptions_and_unmatched_hops_before_sending(target):
    async def scenario():
        sent = []

        def handler(request):
            sent.append(str(request.url))
            return httpx.Response(302, headers={"Location": target})

        shared = gateway(handler)
        try:
            session = approved_operation(
                shared,
                origins=AUTHORIZED_ORIGINS,
                plugin_id=None,
                operation_url="https://www.example.com/page",
                auth_flow_factory=lambda _: Flow(),
                settings=PluginSettings.model_validate(
                    {
                        "endpoints": {
                            "all": {"origin": ["https://*.example.com", "http://*.example.com"], "auth": "session"},
                            "public": {"origin": "https://public.example.com", "auth": "none"},
                        }
                    }
                ),
            )
            with pytest.raises(AuthScopeError):
                await session.execute(RequestSpec("https://www.example.com/start"))
            assert sent == ["https://www.example.com/start"]
        finally:
            await shared.close()

    asyncio.run(scenario())


def test_authenticated_redirect_checks_every_hop_without_reapplying_or_reverifying():
    async def scenario():
        sent = []

        def handler(request):
            sent.append((request.url.host, request.headers.get("Authorization")))
            target = {
                "www.example.com": "https://cdn.example.com/image",
                "cdn.example.com": "https://other.example.com/image",
            }.get(request.url.host)
            return httpx.Response(302, headers={"Location": target}) if target else httpx.Response(200)

        shared = gateway(handler)
        flow = Flow()
        try:
            session = approved_operation(
                shared,
                origins=AUTHORIZED_ORIGINS,
                plugin_id=None,
                operation_url="https://www.example.com/page",
                auth_flow_factory=lambda _: flow,
                settings=PluginSettings.model_validate(
                    {
                        "endpoints": {
                            "all": {
                                "origin": "https://*.${operation.site_domain}",
                                "auth": "session",
                                "verification": {"before_request": True},
                            },
                        }
                    }
                ),
            )
            await session.execute(RequestSpec("https://www.example.com/start"))
            assert sent == [
                (host, "Bearer test-token") for host in ("www.example.com", "cdn.example.com", "other.example.com")
            ]
            assert (flow.initializations, flow.verifications, flow.applications) == (1, 1, 1)
        finally:
            await shared.close()

    asyncio.run(scenario())


def test_auth_hook_uses_declared_wildcards_none_and_operation_but_rejects_unknown_redirect():
    async def scenario():
        sent = []

        def handler(request):
            sent.append(request.url.host)
            if request.url.path == "/redirect":
                return httpx.Response(302, headers={"Location": "https://unlisted.org/login"})
            return httpx.Response(200)

        class Login(Flow):
            async def initialize(self, context):
                return None

            async def login(self, context):
                assert context.endpoints["operation"].origin == "https://example.com:443"
                await context.requests.execute(RequestSpec("https://example.com/bootstrap"))
                await context.requests.execute(RequestSpec("https://login.example.com/login", auth=AuthMode.NONE))
                await context.requests.execute(RequestSpec("https://public.example.com/check"))
                with pytest.raises(AuthScopeError):
                    await context.requests.execute(RequestSpec("https://unknown.org/login", auth=AuthMode.NONE))
                with pytest.raises(AuthScopeError):
                    await context.requests.execute(RequestSpec("https://login.example.com/redirect"))
                return await super().initialize(context)

        shared = gateway(handler)
        try:
            session = approved_operation(
                shared,
                origins=AUTHORIZED_ORIGINS,
                plugin_id=None,
                operation_url="https://example.com/page",
                auth_flow_factory=lambda _: Login(),
                settings=PluginSettings.model_validate(
                    {
                        "endpoints": {
                            "all": {"origin": "https://*.example.com", "auth": "session"},
                            "public": {"origin": "https://public.example.com", "auth": "none"},
                        }
                    }
                ),
            )
            await session.execute(RequestSpec("https://cdn.example.com/image", auth=AuthMode.REQUIRED))
            assert sent == [
                "example.com",
                "login.example.com",
                "public.example.com",
                "login.example.com",
                "cdn.example.com",
            ]
        finally:
            await shared.close()

    asyncio.run(scenario())


def test_explicit_wildcard_policy_overrides_implicit_operation_default():
    async def scenario():
        flow = Flow()
        sent = []
        shared = gateway(lambda request: sent.append(request.headers.get("Authorization")) or httpx.Response(200))
        try:
            session = approved_operation(
                shared,
                origins=AUTHORIZED_ORIGINS,
                plugin_id=None,
                operation_url="https://www.example.com/page",
                auth_flow_factory=lambda _: flow,
                settings=PluginSettings.model_validate(
                    {
                        "endpoints": {
                            "public": {"origin": "https://*.example.com", "auth": "none"},
                        }
                    }
                ),
            )
            assert session.endpoints["operation"].origin == "https://www.example.com:443"
            await session.execute(session.endpoints["operation"].request("/image"))
            assert sent == [None]
            assert flow.initializations == 0
        finally:
            await shared.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "origin", ["https://*.example.com", ["https://example.com", "https://cdn.example.com"], "https://other.example.com"]
)
def test_reserved_operation_name_requires_one_concrete_origin(origin):
    async def scenario():
        shared = gateway(lambda request: httpx.Response(200))
        try:
            with pytest.raises(AuthScopeError, match="reserved"):
                approved_operation(
                    shared,
                    origins=AUTHORIZED_ORIGINS,
                    plugin_id=None,
                    operation_url="https://example.com/page",
                    settings=PluginSettings.model_validate(
                        {
                            "endpoints": {"operation": {"origin": origin, "auth": "none"}},
                        }
                    ),
                )
        finally:
            await shared.close()

    asyncio.run(scenario())


def test_invalid_expansion_fails_before_auth_factory_and_session_declarations_require_flow():
    async def scenario():
        shared = gateway(lambda request: pytest.fail("must not send"))
        try:
            with pytest.raises(ConfigurationError, match="endpoints.images.origin"):
                approved_operation(
                    shared,
                    origins=AUTHORIZED_ORIGINS,
                    plugin_id=None,
                    operation_url="https://example.com",
                    settings=PluginSettings.model_validate(
                        {
                            "endpoints": {
                                "images": {"origin": "https://${config.missing}", "auth": "session"},
                            }
                        }
                    ),
                    auth_flow_factory=lambda _: pytest.fail("invalid rules must precede auth_flow"),
                )
            with pytest.raises(AuthScopeError, match="auth_flow returned None"):
                approved_operation(
                    shared,
                    origins=AUTHORIZED_ORIGINS,
                    plugin_id=None,
                    operation_url="https://example.com",
                    settings=PluginSettings.model_validate(
                        {
                            "endpoints": {
                                "images": {"origin": "https://*.example.com", "auth": "session"},
                            }
                        }
                    ),
                )
            session = approved_operation(
                shared,
                origins=AUTHORIZED_ORIGINS,
                plugin_id=None,
                operation_url="https://example.com",
                settings=PluginSettings.model_validate(
                    {
                        "endpoints": {
                            "operation": {"origin": "${operation.origin}", "auth": "none"},
                        }
                    }
                ),
            )
            assert session.endpoints["operation"].origin == "https://example.com:443"
        finally:
            await shared.close()

    asyncio.run(scenario())
