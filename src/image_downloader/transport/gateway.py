"""Shared HTTP transport and operation-scoped authentication gateway."""

from __future__ import annotations

import asyncio
import random
import time
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from http.cookiejar import CookieJar
from pathlib import Path
from typing import TypedDict, Unpack
from urllib.parse import urlparse

import httpx

from ..configuration.hosts import normalize_host, registrable_domain
from ..configuration.models import AppConfig
from ..exceptions import (
    AuthenticationError,
    ConfigurationError,
    HttpStatusError,
    HttpTransportError,
    PluginError,
    RedirectPolicyError,
    ResponseSizeLimitError,
    UnsupportedSiteFeature,
)
from ..immutable import thaw_json
from ..models import (
    ImageFetchRequest,
    ImageResource,
    ImageTransportMetadata,
    RequestResponse,
    RequestSpec,
    TransportCookie,
    TransportRequestMetadata,
)
from ..observability.logging import DownloadLogger
from ..plugins.plugin_invoker import PluginInvoker
from ..ports import (
    AuthFlow,
    PluginExecutionContext,
    SitePlugin,
    is_auth_flow,
    is_origin_scoped_auth_flow,
)


class _GatewayLogFields(TypedDict, total=False):
    chapter_id: str | None
    url: str | None
    path: str | Path | None
    method: str | None
    status: int | None
    bytes_count: int | None
    count: int | None
    attempt: int | None
    action: str | None
    error: Exception | None
    include_chapter: bool


def _request_origin(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigurationError("request URL must be absolute HTTP(S)")
    host, _ = normalize_host(parsed.hostname)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ConfigurationError("request URL contains an invalid port") from exc
    default_port = 443 if parsed.scheme == "https" else 80
    return f"{parsed.scheme}://{host}:{port or default_port}"


def _header_values(headers: httpx.Headers) -> dict[str, tuple[str, ...]]:
    values: dict[str, list[str]] = {}
    for name, value in headers.multi_items():
        values.setdefault(name.lower(), []).append(value)
    return {name: tuple(items) for name, items in values.items()}


def _request_cookies(headers: httpx.Headers) -> tuple[TransportCookie, ...]:
    cookies: list[TransportCookie] = []
    for value in headers.get_list("cookie"):
        for item in value.split(";"):
            name, separator, cookie_value = item.strip().partition("=")
            if separator and name:
                cookies.append(TransportCookie(name, cookie_value))
    return tuple(cookies)


def _request_metadata(request: httpx.Request) -> TransportRequestMetadata:
    return TransportRequestMetadata(
        str(request.url),
        _header_values(request.headers),
        _request_cookies(request.headers),
    )


def _response_header_values(headers: Mapping[str, str]) -> dict[str, tuple[str, ...]]:
    values: dict[str, list[str]] = {}
    for name, value in headers.items():
        values.setdefault(name.lower(), []).append(value)
    return {name: tuple(items) for name, items in values.items()}


def _spec_metadata(spec: RequestSpec) -> TransportRequestMetadata:
    headers = _response_header_values(spec.headers)
    if spec.referer and "referer" not in headers:
        headers["referer"] = (spec.referer,)
    return TransportRequestMetadata(
        spec.url,
        headers,
        tuple(TransportCookie(name, value) for name, value in spec.cookies.items()),
    )


@dataclass(slots=True)
class _TransportTrace:
    initial_request: TransportRequestMetadata | None = None
    final_request: TransportRequestMetadata | None = None
    response_url: str | None = None
    response_headers: dict[str, tuple[str, ...]] | None = None

    def clear(self) -> None:
        self.initial_request = None
        self.final_request = None
        self.response_url = None
        self.response_headers = None

    def capture(
        self,
        initial_request: TransportRequestMetadata,
        final_request: TransportRequestMetadata,
        response: httpx.Response,
    ) -> None:
        self.initial_request = initial_request
        self.final_request = final_request
        self.response_url = str(response.url)
        self.response_headers = _header_values(response.headers)


@dataclass(frozen=True, slots=True)
class _ImageFetchResult:
    response: RequestResponse
    transport_metadata: ImageTransportMetadata


class RequestGateway:
    """Service-owned HTTP pool, Cookie jar, retry policy, and rate limiters."""

    def __init__(
        self,
        config: AppConfig,
        cookie_jar: CookieJar | None = None,
        *,
        logger: DownloadLogger | None = None,
    ) -> None:
        self.config = config
        network = config.network
        timeout = httpx.Timeout(
            connect=network.connect_timeout_seconds or network.request_timeout_seconds,
            read=network.read_timeout_seconds or network.request_timeout_seconds,
            write=network.write_timeout_seconds or network.request_timeout_seconds,
            pool=network.pool_timeout_seconds or network.request_timeout_seconds,
        )
        self.client = httpx.AsyncClient(
            http2=network.http2,
            timeout=timeout,
            limits=httpx.Limits(
                max_connections=network.pool_max_connections,
                max_keepalive_connections=network.pool_max_idle_connections,
            ),
            # Redirects are followed by this gateway so every hop is checked first.
            follow_redirects=False,
            proxy=network.proxy,
            headers=dict(network.headers),
            cookies=cookie_jar,
        )
        self._global = asyncio.Semaphore(network.request_concurrency)
        self._hosts: defaultdict[str, asyncio.Semaphore] = defaultdict(
            lambda: asyncio.Semaphore(network.origin_request_concurrency or network.request_concurrency)
        )
        self._sites: defaultdict[str, asyncio.Semaphore] = defaultdict(
            lambda: asyncio.Semaphore(
                network.registrable_domain_request_concurrency or network.request_concurrency
            )
        )
        self._interval_lock = asyncio.Lock()
        self._last_request = 0.0
        self._logger = logger

    def operation(
        self,
        *,
        plugin_id: str | None,
        operation_url: str,
        auth_flow_factory: Callable[[OperationRequestGateway], object | None] | None = None,
        invoker: PluginInvoker | None = None,
        request_concurrency: int | None = None,
    ) -> OperationRequestGateway:
        session = OperationRequestGateway(
            self,
            plugin_id=plugin_id,
            operation_url=operation_url,
            invoker=invoker,
            request_concurrency=request_concurrency,
        )
        flow = auth_flow_factory(session) if auth_flow_factory is not None else None
        session._configure_auth_flow(flow)
        return session

    async def execute(self, spec: RequestSpec) -> RequestResponse:
        return await self.operation(plugin_id=None, operation_url=spec.url).execute(spec)

    async def _transport(
        self,
        spec: RequestSpec,
        *,
        plugin_id: str | None = None,
        allowed_redirect_origins: frozenset[str] | None = None,
        trace: _TransportTrace | None = None,
    ) -> RequestResponse:
        retryable_method = spec.method.upper() in {"GET", "HEAD", "OPTIONS", "TRACE"}
        attempts = self.config.network.max_attempts if retryable_method or spec.retry_non_idempotent else 1
        retryable_status = {429, 500, 502, 503, 504}
        last_error: Exception | None = None
        retry_after: float | None = None
        for attempt in range(attempts):
            retry_after = None
            try:
                if trace is not None:
                    trace.clear()
                    response = await self._once(spec, allowed_redirect_origins=allowed_redirect_origins, trace=trace)
                else:
                    response = await self._once(spec, allowed_redirect_origins=allowed_redirect_origins)
                await self._log(
                    "response_received",
                    module="http",
                    url=response.url,
                    method=spec.method,
                    status=response.status,
                    bytes_count=len(response.body),
                    attempt=attempt + 1,
                    plugin_id=plugin_id,
                )
                if response.status not in retryable_status or attempt + 1 == attempts:
                    return response
                retry_after = self._retry_after(response)
                await self._log(
                    "request_retry",
                    module="http",
                    url=spec.url,
                    method=spec.method,
                    status=response.status,
                    attempt=attempt + 1,
                    plugin_id=plugin_id,
                )
            except httpx.TransportError as exc:
                last_error = exc
                await self._log(
                    "request_retry",
                    module="http",
                    url=spec.url,
                    method=spec.method,
                    attempt=attempt + 1,
                    error=exc,
                    plugin_id=plugin_id,
                )
                if attempt + 1 == attempts:
                    raise HttpTransportError("HTTP transport failed after configured attempts") from exc
            base = retry_after if retry_after is not None else 0.25 * (2**attempt)
            capped = min(self.config.network.retry_max_delay_seconds, max(0.0, base))
            jitter = min(0.25, capped * 0.25)
            await asyncio.sleep(max(0.0, capped - random.uniform(0.0, jitter)))
        raise HttpTransportError("HTTP request failed") from last_error

    async def _once(
        self,
        spec: RequestSpec,
        *,
        allowed_redirect_origins: frozenset[str] | None = None,
        trace: _TransportTrace | None = None,
    ) -> RequestResponse:
        headers = dict(spec.headers)
        if spec.referer and not any(key.lower() == "referer" for key in headers):
            headers["Referer"] = spec.referer
        request = self.client.build_request(
            spec.method,
            spec.url,
            headers=headers,
            cookies=dict(spec.cookies) or None,
            params=dict(spec.query) or None,
            data=dict(spec.form) or None,
            json=thaw_json(spec.json),
        )
        initial_request = _request_metadata(request)
        redirects = 0
        while True:
            async with self._send_one_hop(request) as response:
                next_request = response.next_request if self.config.network.follow_redirects else None
                if next_request is not None:
                    if redirects >= self.client.max_redirects:
                        raise RedirectPolicyError(
                            "HTTP redirect limit exceeded",
                            request_url=str(response.url),
                            redirect_url=str(next_request.url),
                            http_status=response.status_code,
                        )
                    try:
                        request = self._checked_redirect(request, next_request, spec, allowed_redirect_origins)
                    except RedirectPolicyError as exc:
                        if exc.request_url is None:
                            exc.request_url = str(response.url)
                            exc.response_url = str(response.url)
                        if exc.redirect_url is None:
                            exc.redirect_url = str(next_request.url)
                        if exc.http_status is None:
                            exc.http_status = response.status_code
                        raise
                    redirects += 1
                    continue
                result = await self._read_response(response)
                if trace is not None:
                    trace.capture(initial_request, _request_metadata(request), response)
                return result

    @asynccontextmanager
    async def _send_one_hop(self, request: httpx.Request) -> AsyncIterator[httpx.Response]:
        url = str(request.url)
        origin = _request_origin(url)
        parsed = urlparse(url)
        normalized_host, is_ip = normalize_host(parsed.hostname or "")
        site = normalized_host if is_ip else registrable_domain(normalized_host)
        async with self._global, self._hosts[origin], self._sites[site]:
            async with self._interval_lock:
                remaining = self.config.network.global_request_interval_seconds - (
                    time.monotonic() - self._last_request
                )
                if remaining > 0:
                    await asyncio.sleep(remaining)
                self._last_request = time.monotonic()
            response = await self.client.send(request, stream=True, auth=None, follow_redirects=False)
            try:
                yield response
            finally:
                await response.aclose()

    @staticmethod
    def _checked_redirect(
        previous: httpx.Request,
        next_request: httpx.Request,
        spec: RequestSpec,
        allowed_origins: frozenset[str] | None,
    ) -> httpx.Request:
        previous_origin = _request_origin(str(previous.url))
        next_origin = _request_origin(str(next_request.url))
        if next_origin == previous_origin:
            return next_request
        if allowed_origins is not None:
            if previous.url.scheme == "https" and next_request.url.scheme == "http":
                raise AuthenticationError("authenticated redirect cannot downgrade to HTTP")
            if next_origin not in allowed_origins:
                raise AuthenticationError("authenticated redirect is outside the configured origins")
            return next_request
        if next_request.method not in {"GET", "HEAD"} or spec.form or spec.json is not None:
            raise RedirectPolicyError("cross-origin redirect with a request body is not allowed")
        # Never forward caller-supplied or client-default headers to an anonymous origin.
        host = next_request.headers.get("Host", next_request.url.netloc.decode("ascii"))
        next_request.headers.clear()
        next_request.headers["Host"] = host
        next_request.headers["Accept"] = "*/*"
        return next_request

    async def _read_response(self, response: httpx.Response) -> RequestResponse:
        if response.headers.get("content-type", "").lower().startswith("text/event-stream"):
            raise UnsupportedSiteFeature("server-sent events are not supported")
        limit = self.config.network.max_response_bytes
        declared = response.headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > limit:
                    raise ResponseSizeLimitError(
                        "HTTP response exceeds the configured byte limit",
                        response_url=str(response.url),
                        http_status=response.status_code,
                        limit_bytes=limit,
                    )
            except ValueError:
                pass
        body = bytearray()
        chunk_size = min(64 * 1024, limit + 1)
        async for chunk in response.aiter_bytes(chunk_size=chunk_size):
            if len(chunk) > limit - len(body):
                raise ResponseSizeLimitError(
                    "HTTP response exceeds the configured byte limit",
                    response_url=str(response.url),
                    http_status=response.status_code,
                    limit_bytes=limit,
                )
            body.extend(chunk)
        return RequestResponse(str(response.url), response.status_code, dict(response.headers), bytes(body))

    @staticmethod
    def _retry_after(response: RequestResponse) -> float | None:
        value = response.headers.get("retry-after")
        if not value:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                target = parsedate_to_datetime(value)
                if target.tzinfo is None:
                    target = target.replace(tzinfo=UTC)
                return max(0.0, (target - datetime.now(UTC)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                return None

    async def _log(
        self,
        event: str,
        *,
        module: str,
        plugin_id: str | None = None,
        **fields: Unpack[_GatewayLogFields],
    ) -> None:
        if self._logger is not None:
            await self._logger.core(event, module=module, debug=True, **fields)

    @staticmethod
    def _validate_protocol(spec: RequestSpec) -> None:
        parsed = urlparse(spec.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise UnsupportedSiteFeature("only absolute HTTP(S) requests are supported")
        if "text/event-stream" in spec.headers.get("Accept", "").lower():
            raise UnsupportedSiteFeature("server-sent events are not supported")

    async def close(self) -> None:
        await self.client.aclose()


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
    ) -> _ImageFetchResult:
        # ``execute_image`` predates the transform-only metadata path. Keep a
        # patched/subclassed compatibility implementation observable rather
        # than silently bypassing it. Such a legacy override cannot provide a
        # physical trace, so expose only an empty, response-derived snapshot.
        implementation = getattr(self.execute_image, "__func__", self.execute_image)
        if implementation is not _DEFAULT_EXECUTE_IMAGE:
            response = await self.execute_image(plugin, image, context)
            if not isinstance(response, RequestResponse):
                raise TypeError("execute_image must return RequestResponse")
            request = TransportRequestMetadata(response.url, {}, ())
            return _ImageFetchResult(
                response,
                ImageTransportMetadata(request, request, response.url, _response_header_values(response.headers)),
            )
        resolution = self._image_fetch_request(await self._invoker.create_image_request(plugin, image, context))
        trace = _TransportTrace()

        async def recover(failed: RequestSpec, response: RequestResponse) -> RequestSpec | None:
            nonlocal resolution
            replacement = await self._invoker.recover_image_request(
                plugin,
                image,
                failed,
                response,
                context,
            )
            if replacement is None:
                return None
            resolution = self._image_fetch_request(replacement, previous_data=resolution.plugin_data)
            return resolution.request

        response = await self._execute(resolution.request, recover=recover, trace=trace)
        initial_request = trace.initial_request or _spec_metadata(resolution.request)
        final_request = trace.final_request or initial_request
        response_url = trace.response_url or response.url
        response_headers = trace.response_headers or _response_header_values(response.headers)
        return _ImageFetchResult(
            response,
            ImageTransportMetadata(
                initial_request,
                final_request,
                response_url,
                response_headers,
                resolution.plugin_data,
            ),
        )

    @staticmethod
    def _image_fetch_request(
        value: RequestSpec | ImageFetchRequest,
        *,
        previous_data: Mapping[str, str] | None = None,
    ) -> ImageFetchRequest:
        if isinstance(value, ImageFetchRequest):
            return value
        return ImageFetchRequest(value, previous_data or {})

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
            if self._request_limit is None:
                if trace is None:
                    response = await self._shared._transport(
                        current,
                        plugin_id=self.plugin_id,
                        allowed_redirect_origins=redirect_origins,
                    )
                else:
                    response = await self._shared._transport(
                        current,
                        plugin_id=self.plugin_id,
                        allowed_redirect_origins=redirect_origins,
                        trace=trace,
                    )
            else:
                async with self._request_limit:
                    if trace is None:
                        response = await self._shared._transport(
                            current,
                            plugin_id=self.plugin_id,
                            allowed_redirect_origins=redirect_origins,
                        )
                    else:
                        response = await self._shared._transport(
                            current,
                            plugin_id=self.plugin_id,
                            allowed_redirect_origins=redirect_origins,
                            trace=trace,
                        )
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


_DEFAULT_EXECUTE_IMAGE = OperationRequestGateway.execute_image
