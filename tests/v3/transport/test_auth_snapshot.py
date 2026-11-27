from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError

import httpx
import pytest
from support.auth import approved_access

from image_downloader import (
    AppConfig,
    AuthenticationError,
    AuthLease,
    AuthSnapshot,
    AuthStateUnknownError,
    AuthStatus,
    AuthVerification,
    BaseAuthFlow,
    RequestSpec,
)
from image_downloader.configuration.models import PluginConnections
from image_downloader.transport.gateway import RequestGateway


class Flow(BaseAuthFlow):
    def __init__(self):
        self.lease = AuthLease(100, public_data={"user_id": "first", "nested": [1, 2]})
        self.verification = AuthVerification(AuthStatus.VALID)
        self.initialized = self.refreshed = self.verified = 0
        self.fail_refresh = False

    async def initialize(self, context):
        self.initialized += 1
        return self.lease

    async def refresh(self, context):
        self.refreshed += 1
        if self.fail_refresh:
            raise AuthenticationError("refresh failed")
        return self.lease

    async def verify(self, context):
        self.verified += 1
        return self.verification


def operation(flow, *, skew=0):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200)

    gateway = RequestGateway(AppConfig(), transport_factory=lambda: httpx.MockTransport(handler))
    session = gateway.operation(
        plugin_id="test.snapshot",
        access=approved_access("https://example.test", plugin_id="test.snapshot"),
        operation_url="https://example.test/gallery",
        auth_flow_factory=lambda _: flow,
        settings=PluginConnections.model_validate({"authentication": {"refresh_skew_seconds": skew}}),
    )
    now = [10.0]
    session.auth._clock = lambda: now[0]
    return gateway, session, now, calls


def test_snapshot_never_initializes_or_sends_and_is_immutable():
    async def scenario():
        flow = Flow()
        gateway, session, _now, calls = operation(flow)
        try:
            initial = session.auth.snapshot()
            assert initial == AuthSnapshot(flow_configured=True)
            assert flow.initialized == 0 and calls == []
            await session.auth.ensure_ready()
            ready = session.auth.snapshot()
            assert ready.credentials_ready and ready.generation == 1
            assert ready.last_verified_status is AuthStatus.UNKNOWN and ready.remaining_seconds == 100
            assert ready.public_data == {"user_id": "first", "nested": (1, 2)}
            with pytest.raises(TypeError):
                ready.public_data["user_id"] = "changed"
            with pytest.raises(FrozenInstanceError):
                ready.generation = 3
            assert calls == []
        finally:
            await gateway.close()
        assert not session.auth.snapshot().credentials_ready and session.auth.snapshot().public_data == {}
        assert ready.credentials_ready and ready.public_data["user_id"] == "first"

    asyncio.run(scenario())


def test_missing_flow_unknown_expiry_and_expired_snapshot():
    async def scenario():
        gateway, session, _now, calls = operation(None)
        try:
            assert session.auth.snapshot() == AuthSnapshot()
            await session.execute(RequestSpec("https://example.test/gallery"))
            assert session.auth.snapshot() == AuthSnapshot() and len(calls) == 1
        finally:
            await gateway.close()
        flow = Flow()
        flow.lease = AuthLease(public_data={"user_id": "unknown-expiry"})
        gateway, session, now, _calls = operation(flow)
        try:
            await session.auth.ensure_ready()
            assert session.auth.snapshot().remaining_seconds is None
            flow.lease = AuthLease(100, public_data={"user_id": "known"})
            response = await session.execute(RequestSpec("https://example.test/gallery"))
            session.auth.invalidate(response)
            await session.auth.ensure_ready()
            now[0] = 111
            snapshot = session.auth.snapshot()
            assert snapshot.remaining_seconds == 0 and not snapshot.credentials_ready and not snapshot.public_data
        finally:
            await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "public_data,expected",
    [(None, {"user_id": "first", "nested": (1, 2)}), ({}, {}), ({"user_id": "verified"}, {"user_id": "verified"})],
)
def test_valid_verification_lease_preserves_replaces_or_clears(public_data, expected):
    async def scenario():
        flow = Flow()
        gateway, session, now, _calls = operation(flow)
        try:
            await session.auth.ensure_ready(check=True)
            assert session.auth.snapshot().public_data["user_id"] == "first"
            now[0] = 20
            flow.verification = AuthVerification(AuthStatus.VALID, AuthLease(50, public_data=public_data))
            await session.auth.ensure_ready(check=True)
            snapshot = session.auth.snapshot()
            assert snapshot.last_verified_status is AuthStatus.VALID and snapshot.generation == 1
            assert snapshot.remaining_seconds == 50 and snapshot.public_data == expected
        finally:
            await gateway.close()

    asyncio.run(scenario())


def test_invalidation_generation_refresh_and_failure_do_not_publish_stale_identity():
    async def scenario():
        flow = Flow()
        gateway, session, now, _calls = operation(flow, skew=10)
        try:
            first_response = await session.execute(RequestSpec("https://example.test/gallery"))
            now[0] = 100
            assert session.auth.snapshot().remaining_seconds == 10
            assert not session.auth.snapshot().credentials_ready and not session.auth.snapshot().public_data
            flow.lease = AuthLease(100, public_data={"user_id": "second"})
            second_response = await session.execute(RequestSpec("https://example.test/gallery"))
            assert session.auth.snapshot().generation == 2
            assert not session.auth.invalidate(first_response)
            assert session.auth.snapshot().public_data == {"user_id": "second"}
            assert session.auth.invalidate(second_response)
            assert not session.auth.snapshot().public_data
            flow.lease = AuthLease()
            await session.auth.ensure_ready()
            assert session.auth.snapshot().credentials_ready and not session.auth.snapshot().public_data
            third = await session.execute(RequestSpec("https://example.test/gallery"))
            session.auth.invalidate(third)
            flow.fail_refresh = True
            with pytest.raises(AuthenticationError, match="refresh failed"):
                await session.auth.ensure_ready()
            assert not session.auth.snapshot().credentials_ready and not session.auth.snapshot().public_data
        finally:
            await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [AuthStatus.INVALID, AuthStatus.UNKNOWN])
def test_verification_failure_hides_public_data(status):
    async def scenario():
        flow = Flow()
        gateway, session, _now, _calls = operation(flow)
        try:
            await session.auth.ensure_ready()
            flow.verification = AuthVerification(status)
            flow.fail_refresh = True
            with pytest.raises((AuthenticationError, AuthStateUnknownError)):
                await session.auth.ensure_ready(check=True)
            snapshot = session.auth.snapshot()
            assert not snapshot.credentials_ready and not snapshot.public_data
            assert snapshot.last_verified_status is status
        finally:
            await gateway.close()

    asyncio.run(scenario())


def test_invalid_verification_then_successful_refresh_resets_verification_status():
    async def scenario():
        flow = Flow()
        gateway, session, _now, _calls = operation(flow)
        try:
            await session.auth.ensure_ready(check=True)
            assert session.auth.snapshot().last_verified_status is AuthStatus.VALID
            flow.verification = AuthVerification(AuthStatus.INVALID)
            flow.lease = AuthLease(200, public_data={"user_id": "refreshed"})
            await session.auth.ensure_ready(check=True)
            snapshot = session.auth.snapshot()
            assert snapshot.credentials_ready and snapshot.generation == 2
            assert snapshot.last_verified_status is AuthStatus.UNKNOWN
            assert snapshot.public_data == {"user_id": "refreshed"} and snapshot.remaining_seconds == 200
        finally:
            await gateway.close()

    asyncio.run(scenario())


def test_initialization_failure_never_publishes_credentials():
    class Broken(Flow):
        async def initialize(self, context):
            raise AuthenticationError("initialization failed")

    async def scenario():
        gateway, session, _now, _calls = operation(Broken())
        try:
            with pytest.raises(AuthenticationError):
                await session.auth.ensure_ready()
            assert session.auth.snapshot() == AuthSnapshot(flow_configured=True)
        finally:
            await gateway.close()

    asyncio.run(scenario())
