"""Transport retries and a fresh operation recover at their own boundaries."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from support.auth import approved_access

from image_downloader import AuthLease, AuthMode, BaseAuthFlow, RequestSpec
from image_downloader.configuration.models import AppConfig
from image_downloader.exceptions import HttpTransportError
from image_downloader.transport.gateway import RequestGateway


class TokenFlow(BaseAuthFlow):
    def __init__(self):
        self.initialized = 0

    async def login(self, context):
        self.initialized += 1
        await context.requests.execute(RequestSpec("https://review.test/token", auth=AuthMode.NONE))
        return AuthLease()


def gateway_for(handler):
    return RequestGateway(
        AppConfig.model_validate(
            {"network": {"max_attempts": 2, "retry_max_delay_seconds": 0, "auth_refresh_attempts": 5}}
        ),
        transport_factory=lambda: httpx.MockTransport(handler),
    )


def operation(gateway, flow):
    return gateway.operation(
        access=approved_access("https://review.test", plugin_id="test.review"),
        plugin_id="test.review",
        operation_url="https://review.test/page",
        auth_flow_factory=lambda _: flow,
    )


def test_auth_transport_retry_recovers_transient_error_without_reinitialization():
    async def scenario():
        calls = []

        def handler(request):
            calls.append(request.url.path)
            if len(calls) == 1:
                raise httpx.ConnectTimeout("temporary failure")
            return httpx.Response(200)

        gateway, flow = gateway_for(handler), TokenFlow()
        try:
            response = await operation(gateway, flow).execute(
                RequestSpec("https://review.test/page", auth=AuthMode.REQUIRED)
            )
            assert response.status == 200
            assert calls == ["/token", "/token", "/page"]
            assert flow.initialized == 1
        finally:
            await gateway.close()

    asyncio.run(scenario())


def test_exhausted_failure_is_shared_but_next_operation_can_recover():
    async def scenario():
        calls = []

        def handler(request):
            calls.append(request.url.path)
            if len(calls) <= 2:
                raise httpx.ConnectTimeout("temporary failure")
            return httpx.Response(200)

        gateway, flow = gateway_for(handler), TokenFlow()
        session = operation(gateway, flow)
        try:
            with pytest.raises(HttpTransportError) as first:
                await session.execute(RequestSpec("https://review.test/page", auth=AuthMode.REQUIRED))
            with pytest.raises(HttpTransportError) as second:
                await session.execute(RequestSpec("https://review.test/page", auth=AuthMode.REQUIRED))
            assert first.value is second.value
            assert calls == ["/token", "/token"]
            assert flow.initialized == 1
            response = await operation(gateway, TokenFlow()).execute(
                RequestSpec("https://review.test/page", auth=AuthMode.REQUIRED)
            )
            assert response.status == 200
            assert calls == ["/token", "/token", "/token", "/page"]
        finally:
            await gateway.close()

    asyncio.run(scenario())
