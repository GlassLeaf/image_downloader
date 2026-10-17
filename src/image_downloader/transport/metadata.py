"""Request snapshots, origin checks and physical transport metadata."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict
from urllib.parse import urlparse

import httpx

from ..configuration.hosts import normalize_host
from ..exceptions import (
    ConfigurationError,
)
from ..models import (
    EffectiveRequestPreview,
    ImageTransportMetadata,
    RequestResponse,
    RequestSpec,
    TransportCookie,
    TransportHeader,
    TransportRequestMetadata,
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


def _effective_request_preview(request: httpx.Request) -> EffectiveRequestPreview:
    """Capture every serialisable request value just before ``client.send``."""
    return EffectiveRequestPreview(
        request.method,
        str(request.url),
        tuple(TransportHeader(name, value) for name, value in request.headers.multi_items()),
        _request_cookies(request.headers),
        request.content,
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
class ImageFetchResult:
    response: RequestResponse
    transport_metadata: ImageTransportMetadata
