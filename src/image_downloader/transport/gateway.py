"""Shared HTTP pool, retries, request limits and checked redirect transport."""

from __future__ import annotations

import asyncio
import random
import sys
import time
from collections import defaultdict
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from http.cookiejar import CookieJar
from typing import Unpack
from urllib.parse import urlparse

import httpx

from ..configuration.hosts import normalize_host, registrable_domain
from ..configuration.models import AppConfig
from ..exceptions import (
    AuthenticationError,
    HttpTransportError,
    RedirectPolicyError,
    ResponseSizeLimitError,
    UnsupportedSiteFeatureError,
)
from ..immutable import thaw_json
from ..models import (
    RequestResponse,
    RequestSpec,
)
from ..observability.logging import DownloadLogger
from ..plugins.plugin_invoker import PluginInvoker
from .metadata import (
    _GatewayLogFields,
    _request_metadata,
    _request_origin,
    _TransportTrace,
)
from .operation import OperationRequestGateway


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
        self._global = asyncio.Semaphore(network.request_concurrency)
        self._hosts: defaultdict[str, asyncio.Semaphore] = defaultdict(
            lambda: asyncio.Semaphore(network.origin_request_concurrency or network.request_concurrency)
        )
        self._sites: defaultdict[str, asyncio.Semaphore] = defaultdict(
            lambda: asyncio.Semaphore(network.registrable_domain_request_concurrency or network.request_concurrency)
        )
        self._interval_lock = asyncio.Lock()
        self._last_request = 0.0
        self._logger = logger
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
        request = self._build_request(spec)
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

    def _build_request(self, spec: RequestSpec) -> httpx.Request:
        """Build the request representation shared by preview and transport."""
        headers = dict(spec.headers)
        if spec.referer and not any(key.lower() == "referer" for key in headers):
            headers["Referer"] = spec.referer
        return self.client.build_request(
            spec.method,
            spec.url,
            headers=headers,
            cookies=dict(spec.cookies) or None,
            params=dict(spec.query) or None,
            data=dict(spec.form) or None,
            json=thaw_json(spec.json),
        )

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
            raise UnsupportedSiteFeatureError("server-sent events are not supported")
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
            raise UnsupportedSiteFeatureError("only absolute HTTP(S) requests are supported")
        if "text/event-stream" in spec.headers.get("Accept", "").lower():
            raise UnsupportedSiteFeatureError("server-sent events are not supported")

    async def close(self) -> None:
        await self.client.aclose()


@asynccontextmanager
async def closing_gateway(gateway: RequestGateway) -> AsyncIterator[RequestGateway]:
    """Close an owned session without replacing its operation's primary failure."""
    try:
        yield gateway
    except BaseException:
        try:
            await gateway.close()
        except BaseException:
            # Detached sessions must not persist logs or expose exception text.
            try:
                print("warning: HTTP session cleanup failed; original failure is unchanged", file=sys.stderr)
            except BaseException:
                pass
        raise
    else:
        await gateway.close()
