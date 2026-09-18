"""Small validation helpers shared by the offline plugin examples."""

from __future__ import annotations

import json
from collections.abc import Mapping
from urllib.parse import urlparse

from image_downloader import PluginError, RequestResponse


def path_id(url: str) -> str:
    value = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    if not value:
        raise PluginError("example URL is missing its resource ID")
    return value


def origin(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise PluginError("example URL must be an HTTP URL")
    return f"{parsed.scheme}://{parsed.netloc}"


def response_json(response: RequestResponse, *, message: str) -> Mapping[str, object]:
    try:
        payload = json.loads(response.body)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PluginError(message) from exc
    if not isinstance(payload, Mapping):
        raise PluginError(message)
    return payload


def required_text(value: object, *, message: str) -> str:
    if not isinstance(value, str) or not value:
        raise PluginError(message)
    return value
