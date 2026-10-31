"""Fixture replay and request recording at the actual HTTP transport boundary."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

import httpx
import yaml

from .output import RawOutput, read_document


class FixtureMismatchError(RuntimeError):
    """A fixture cannot satisfy the next physical request; never fall back to HTTP."""


def _text_body(body: bytes, content_type: str) -> str | None:
    if content_type.startswith("text/") or any(token in content_type for token in ("json", "xml", "form-urlencoded")):
        try:
            return body.decode("utf-8")
        except UnicodeError:
            return None
    return None


class FixtureTransport(httpx.AsyncBaseTransport):
    def __init__(self, path: Path) -> None:
        document = read_document(path)
        self.entries: list[dict[str, Any]] = document["requests"]
        self.secrets: dict[str, str] = document.get("secrets", {})
        self.root = path.parent
        self.positions: dict[int, int] = {}
        if not isinstance(self.entries, list):
            raise ValueError("fixture requests must be a list")

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        for index, entry in enumerate(self.entries):
            match = entry.get("match", entry)
            if match.get("method", "GET").upper() != request.method or match["url"] != str(request.url):
                continue
            if not self._body_matches(match, body):
                continue
            if any(request.headers.get(key) != val for key, val in match.get("headers", {}).items()):
                continue
            responses = entry.get("responses", [entry.get("response", {})])
            position = self.positions.get(index, 0)
            if position >= len(responses):
                if not entry.get("repeat_last", False):
                    continue
                position = len(responses) - 1
            self.positions[index] = self.positions.get(index, 0) + 1
            response = responses[position]
            if "error" in response:
                raise httpx.ConnectError(response["error"], request=request)
            if "body_file" in response:
                content = (self.root / response["body_file"]).read_bytes()
            elif "body_base64" in response:
                content = base64.b64decode(response["body_base64"])
            elif "json" in response:
                content = json.dumps(response["json"], ensure_ascii=False).encode()
            else:
                content = str(response.get("body", "")).encode()
            headers = response.get("headers", {})
            if "json" in response and not headers:
                headers = {"content-type": "application/json"}
            return httpx.Response(
                response.get("status", 200), headers=headers, stream=httpx.ByteStream(content), request=request
            )
        raise FixtureMismatchError(f"no remaining fixture response: {request.method} {request.url} body={body!r}")

    @staticmethod
    def _body_matches(match: dict[str, Any], body: bytes) -> bool:
        if "body_sha256" in match and hashlib.sha256(body).hexdigest() != match["body_sha256"]:
            return False
        if "body" in match and body != str(match["body"]).encode():
            return False
        if "json" in match:
            try:
                return bool(json.loads(body) == match["json"])
            except (ValueError, UnicodeError):
                return False
        return True

    def remaining(self) -> list[dict[str, Any]]:
        return [
            {
                "match": entry.get("match", entry),
                "remaining": max(
                    0, len(entry.get("responses", [entry.get("response", {})])) - self.positions.get(index, 0)
                ),
            }
            for index, entry in enumerate(self.entries)
        ]


class TraceTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport, output: RawOutput, *, record: bool = False) -> None:
        self.inner, self.output, self.record = inner, output, record
        self.recorded = output.recorded_http

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        self.output.event(
            "http_request",
            method=request.method,
            url=str(request.url),
            headers=list(request.headers.multi_items()),
            body=body,
            body_text=_text_body(body, request.headers.get("content-type", "")),
        )
        try:
            response = await self.inner.handle_async_request(request)
            response.stream = TracedStream(cast(httpx.AsyncByteStream, response.stream), self, request, response, body)
            return response
        except BaseException as exc:
            self.output.event("http_error", url=str(request.url), error=exc)
            raise

    async def aclose(self) -> None:
        await self.inner.aclose()

    def received(
        self, request: httpx.Request, response: httpx.Response, body: bytes, content: bytes, complete: bool
    ) -> None:
        self.output.event(
            "http_response",
            url=str(request.url),
            status=response.status_code,
            headers=list(response.headers.multi_items()),
            body=content,
            body_text=_text_body(content, response.headers.get("content-type", "")),
            complete=complete,
        )
        if not self.record or not complete:
            return
        self.recorded.append(
            {
                "match": {
                    "method": request.method,
                    "url": str(request.url),
                    "body_sha256": hashlib.sha256(body).hexdigest(),
                },
                "response": {
                    "status": response.status_code,
                    "headers": list(response.headers.multi_items()),
                    "body_base64": base64.b64encode(content).decode(),
                },
            }
        )
        grouped: dict[str, dict[str, Any]] = {}
        for item in self.recorded:
            key = json.dumps(item["match"], sort_keys=True)
            grouped.setdefault(key, {"match": item["match"], "responses": []})["responses"].append(item["response"])
        (self.output.directory / "recording.yaml").write_text(
            yaml.safe_dump({"requests": list(grouped.values())}, allow_unicode=True), encoding="utf-8"
        )


class TracedStream(httpx.AsyncByteStream):
    def __init__(
        self,
        stream: httpx.AsyncByteStream,
        owner: TraceTransport,
        request: httpx.Request,
        response: httpx.Response,
        body: bytes,
    ) -> None:
        self.stream, self.owner, self.request, self.response, self.body = stream, owner, request, response, body
        self.parts = bytearray()
        self.complete = False
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for part in self.stream:
            self.parts.extend(part)
            yield part
        self.complete = True

    async def aclose(self) -> None:
        if not self.closed:
            self.closed = True
            try:
                self.owner.received(self.request, self.response, self.body, bytes(self.parts), self.complete)
            finally:
                await self.stream.aclose()
