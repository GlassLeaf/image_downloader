"""One operation authentication session and its bounded request execution."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import nullcontext
from typing import TYPE_CHECKING, Unpack

from ..exceptions import (
    AuthenticationError,
    HttpStatusError,
    PluginError,
)
from ..models import (
    EffectiveRequestPreview,
    ImageResource,
    RequestResponse,
    RequestSpec,
)
from ..plugins.plugin_invoker import PluginInvoker
from ..ports import (
    AuthFlow,
    PluginExecutionContext,
    SitePlugin,
    is_auth_flow,
    is_origin_scoped_auth_flow,
)
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
    ) -> None:
        self._shared = shared
        self.plugin_id = plugin_id
        self._invoker = invoker or PluginInvoker(plugin_id or "core.request", shared._logger)
        self._auth_flow: AuthFlow | None = None
        self._auth_origins = frozenset((_request_origin(operation_url),))
        self._auth_lock = asyncio.Lock()
        self._auth_generation = 0
        self._request_limit = asyncio.Semaphore(request_concurrency) if request_concurrency is not None else None

    def _configure_auth_flow(self, flow: object | None) -> None:
        if flow is not None and not is_auth_flow(flow):
            raise PluginError("auth_flow must return AuthFlow or None")
        origins = set(self._auth_origins)
        if flow is not None and is_origin_scoped_auth_flow(flow):
            declared = flow.allowed_origins
            if not isinstance(declared, (tuple, list, set, frozenset)) or any(
                not isinstance(value, str) for value in declared
            ):
                raise PluginError("AuthFlow.allowed_origins must be a sequence of HTTP(S) origins")
            origins.update(_request_origin(value) for value in declared)
        self._auth_flow = flow
        self._auth_origins = frozenset(origins)

    async def execute(self, spec: RequestSpec) -> RequestResponse:
        return await self._execute(spec)

    async def preview(self, spec: RequestSpec) -> EffectiveRequestPreview:
        """Resolve the no-send request snapshot used by inspection.

        This intentionally follows the normal protocol validation and auth
        application path, but does not acquire request limits or send a hop.
        """
        self._shared._validate_protocol(spec)
        current = await self._apply_auth(spec)
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
        current = await self._apply_auth(spec)
        auth_attempts = 0
        recovered = False
        while True:
            observed_generation = self._auth_generation
            redirect_origins = self._auth_origins if current.auth_required and self._auth_flow is not None else None
            async with self._request_limit if self._request_limit is not None else nullcontext():
                response = await self._send(current, redirect_origins, trace)
            if current.auth_required and self._is_auth_failure(current, response):
                if auth_attempts >= self._shared.config.network.auth_refresh_attempts:
                    raise AuthenticationError("authentication failed after configured refresh attempts")
                async with self._auth_lock:
                    if observed_generation == self._auth_generation:
                        if self._auth_flow is None:
                            raise AuthenticationError("authentication is required")
                        await self._log("auth_refresh_started", module="auth", url=current.url)
                        replacement = await self._invoker.refresh_auth(self._auth_flow, current, response)
                        if replacement is None:
                            raise AuthenticationError("authentication refresh was declined")
                        self._auth_generation += 1
                        current = replacement
                        await self._log("auth_refresh_finished", module="auth", url=current.url)
                    else:
                        current = spec
                auth_attempts += 1
                current = await self._apply_auth(current)
                continue
            if response.status >= 400 and recover is not None and not recovered:
                replacement = await recover(current, response)
                recovered = True
                if replacement is not None:
                    current = await self._apply_auth(replacement)
                    continue
            if response.status >= 400:
                raise HttpStatusError(response.status, response_url=response.url)
            return response

    async def _send(
        self,
        request: RequestSpec,
        redirect_origins: frozenset[str] | None,
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

    async def _apply_auth(self, spec: RequestSpec) -> RequestSpec:
        if spec.auth_required and self._auth_flow is not None:
            if _request_origin(spec.url) not in self._auth_origins:
                raise AuthenticationError("authentication cannot be applied outside the configured origins")
            await self._log("auth_apply_started", module="auth", url=spec.url, action="apply")
            result = await self._invoker.apply_auth(self._auth_flow, spec)
            if _request_origin(result.url) not in self._auth_origins:
                raise AuthenticationError("AuthFlow.apply changed the request to a disallowed origin")
            await self._log("auth_apply_finished", module="auth", url=result.url, action="apply")
            return result
        return spec

    def _is_auth_failure(self, request: RequestSpec, response: RequestResponse) -> bool:
        if response.status in {401, 403}:
            return True
        return bool(self._auth_flow and self._invoker.is_auth_failure(self._auth_flow, request, response))

    async def _log(self, event: str, *, module: str, **fields: Unpack[_GatewayLogFields]) -> None:
        await self._shared._log(event, module=module, plugin_id=self.plugin_id, **fields)
