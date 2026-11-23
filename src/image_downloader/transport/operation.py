"""One operation authentication session and its bounded request execution."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from contextlib import nullcontext
from dataclasses import replace
from typing import TYPE_CHECKING, Unpack, cast

from ..configuration.models import PluginConnections, PluginSettings
from ..exceptions import (
    AuthenticationError,
    AuthScopeError,
    HttpStatusError,
    SecretNotFoundError,
)
from ..immutable import freeze_json
from ..models import (
    AuthReceipt,
    EffectiveRequestPreview,
    ImageResource,
    RequestResponse,
    RequestSpec,
)
from ..plugins.plugin_invoker import PluginInvoker
from ..ports import (
    PluginExecutionContext,
    SecretProvider,
    SitePlugin,
)
from .auth import OperationAuthSession
from .image_requests import fetch_image
from .metadata import (
    ImageFetchResult,
    _effective_request_preview,
    _GatewayLogFields,
    _request_origin,
    _TransportTrace,
)

if TYPE_CHECKING:
    from .gateway import RequestGateway


class OperationRequestGateway:
    """Authentication and diagnostics scoped to one plugin operation."""

    def __init__(
        self,
        shared: RequestGateway,
        *,
        plugin_id: str | None,
        operation_url: str,
        invoker: PluginInvoker | None = None,
        request_concurrency: int | None = None,
        settings: PluginSettings | PluginConnections | None = None,
        plugin_config: Mapping[str, object] | None = None,
    ) -> None:
        self._shared = shared
        self.plugin_id = plugin_id
        self._invoker = invoker or PluginInvoker(plugin_id or "core.request", shared._logger)
        selected_settings = settings or PluginSettings()
        config = cast(
            Mapping[str, object],
            freeze_json(
                plugin_config
                if plugin_config is not None
                else selected_settings.config
                if isinstance(selected_settings, PluginSettings)
                else {}
            ),
        )
        self.auth = OperationAuthSession(operation_url, selected_settings, self._invoker, plugin_config=config)
        self.endpoints = self.auth.endpoints
        self._request_limit = asyncio.Semaphore(request_concurrency) if request_concurrency is not None else None
        self.bind_auth_context(config, _UnavailableSecrets())

    def bind_auth_context(self, config: Mapping[str, object], secrets: SecretProvider) -> None:
        self.auth.bind(config, secrets, _AuthenticationRequests(self))

    def _configure_auth_flow(self, flow: object | None) -> None:
        self.auth.configure(flow)

    async def close(self) -> None:
        await self.auth.close()

    async def execute(self, spec: RequestSpec) -> RequestResponse:
        return await self._execute(spec)

    async def preview(self, spec: RequestSpec) -> EffectiveRequestPreview:
        """Resolve the no-send request snapshot used by inspection.

        This intentionally follows the normal protocol validation and auth
        application path, but does not acquire request limits or send a hop.
        """
        self._shared._validate_protocol(spec)
        current, _receipt = await self._prepare(spec)
        return _effective_request_preview(self._shared._build_request(current))

    async def execute_image(
        self,
        plugin: SitePlugin,
        image: ImageResource,
        context: PluginExecutionContext,
    ) -> RequestResponse:
        """Compatibility image-fetch API without transform-only metadata."""
        return (await self.execute_image_with_metadata(plugin, image, context)).response

    async def execute_image_with_metadata(
        self,
        plugin: SitePlugin,
        image: ImageResource,
        context: PluginExecutionContext,
    ) -> ImageFetchResult:
        return await fetch_image(self._invoker, self._execute, plugin, image, context)

    async def _execute(
        self,
        spec: RequestSpec,
        recover: Callable[[RequestSpec, RequestResponse], Awaitable[RequestSpec | None]] | None = None,
        trace: _TransportTrace | None = None,
    ) -> RequestResponse:
        self._shared._validate_protocol(spec)
        await self._log("request_started", module="http", url=spec.url, method=spec.method)
        base = spec
        auth_attempts = 0
        recovered = False
        while True:
            current, receipt = await self._prepare(base)
            redirect_origins = self.auth.allows_authenticated_origin if receipt is not None else None
            async with self._request_limit if self._request_limit is not None else nullcontext():
                response = await self._send(current, redirect_origins, trace)
            response = replace(response, auth_receipt=receipt)
            if receipt is not None and await self.auth.response_failed(current, response):
                if auth_attempts >= self._shared.config.network.auth_refresh_attempts:
                    raise AuthenticationError("authentication failed after configured refresh attempts")
                if (
                    current.method.upper() not in {"GET", "HEAD", "OPTIONS", "TRACE"}
                    and not current.retry_non_idempotent
                ):
                    raise AuthenticationError("authentication retry requires retry_non_idempotent=True for this method")
                auth_attempts += 1
                continue
            if response.status >= 400 and recover is not None and not recovered:
                replacement = await recover(current, response)
                recovered = True
                if replacement is not None:
                    base = replacement
                    continue
            if response.status >= 400:
                raise HttpStatusError(response.status, response_url=response.url)
            return response

    async def _send(
        self,
        request: RequestSpec,
        redirect_origins: Callable[[str], bool] | None,
        trace: _TransportTrace | None,
    ) -> RequestResponse:
        if trace is None:
            return await self._shared._transport(
                request,
                plugin_id=self.plugin_id,
                allowed_redirect_origins=redirect_origins,
            )
        return await self._shared._transport(
            request,
            plugin_id=self.plugin_id,
            allowed_redirect_origins=redirect_origins,
            trace=trace,
        )

    async def _prepare(self, spec: RequestSpec) -> tuple[RequestSpec, AuthReceipt | None]:
        endpoint = self.auth.resolve(spec)
        if endpoint is None:
            return spec, None
        await self.auth.prepare(endpoint)
        await self._log("auth_apply_started", module="auth", url=spec.url, action="apply")
        result, receipt = self.auth.apply(spec)
        await self._log("auth_apply_finished", module="auth", url=result.url, action="apply")
        return result, receipt

    async def _log(self, event: str, *, module: str, **fields: Unpack[_GatewayLogFields]) -> None:
        await self._shared._log(event, module=module, plugin_id=self.plugin_id, **fields)


class _UnavailableSecrets:
    def get(self, name: str) -> str:
        raise SecretNotFoundError("no secret provider is bound to this operation")


class _AuthenticationRequests:
    """The non-reentrant HTTP port passed exclusively to authentication hooks."""

    def __init__(self, gateway: OperationRequestGateway) -> None:
        self._gateway = gateway

    async def execute(self, spec: RequestSpec) -> RequestResponse:
        gateway = self._gateway
        if gateway.auth._closed:
            raise AuthScopeError("authentication operation is closed")
        gateway._shared._validate_protocol(spec)
        if not gateway.auth.allows_declared_origin(_request_origin(spec.url)):
            raise AuthScopeError("authentication request origin is undeclared; add an endpoints declaration")
        async with gateway._request_limit if gateway._request_limit is not None else nullcontext():
            return await gateway._shared._transport(
                spec,
                plugin_id=gateway.plugin_id,
                allowed_endpoint_origins=gateway.auth.allows_declared_origin,
            )
