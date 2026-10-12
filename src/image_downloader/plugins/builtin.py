"""Trusted core-supplied v3 fallback plugin."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from html.parser import HTMLParser
from ipaddress import IPv6Address
from urllib.parse import urljoin, urlparse

import httpx

from ..models import (
    Chapter,
    DownloadManifest,
    ImageArtifact,
    ImageResource,
    RequestResponse,
    RequestSpec,
    UpdateCandidate,
    UpdateSnapshot,
)
from ..ports import PluginExecutionContext, TransformContext
from .html_encoding import _decode_html


def _referer_origin(url: httpx.URL) -> tuple[str, bytes, int]:
    host = url.raw_host
    if b":" in host:
        host = IPv6Address(host.decode("ascii")).compressed.encode("ascii")
    port = url.port if url.port is not None else (443 if url.scheme == "https" else 80)
    return url.scheme, host, port


def _automatic_referer(source_url: str, target_url: str) -> str | None:
    """Limit only the built-in HTML plugin's automatically generated Referer."""
    try:
        source, target = httpx.URL(source_url), httpx.URL(target_url)
        for value, url in ((source_url, source), (target_url, target)):
            parsed = urlparse(value)
            if (
                url.scheme not in {"http", "https"}
                or not url.is_absolute_url
                or (parsed.port is not None and not 0 <= parsed.port <= 65535)
            ):
                return None
        if source.scheme == "https" and target.scheme == "http":
            return None
        clean = source.copy_with(username=None, password=None, fragment=None)
        if _referer_origin(source) == _referer_origin(target):
            return str(clean)
        return str(clean.copy_with(path="/", query=None))
    except (httpx.InvalidURL, ValueError):
        return None


class _ImageParser(HTMLParser):
    def __init__(self, source_url: str) -> None:
        super().__init__()
        self.source_url = source_url
        self.title = "download"
        self._images: list[ImageResource] = []
        self._base_url = source_url
        self._base_seen = False
        self._in_title = False
        self._title: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "base" and "href" in values and not self._base_seen:
            # Only the first base href controls the document. Unsupported or
            # malformed bases fall back to the final response URL.
            self._base_seen = True
            try:
                candidate = urljoin(self.source_url, values["href"].strip())
                parsed = urlparse(candidate)
                if parsed.scheme in {"http", "https"} and parsed.hostname:
                    self._base_url = candidate
            except ValueError:
                pass
        if tag.lower() == "title":
            self._in_title = True
        if tag.lower() == "img" and values.get("src"):
            self._images.append(
                ImageResource(
                    values["src"],
                    len(self._images) + 1,
                    referer=self.source_url,
                    image_id=values.get("data-image-id") or None,
                )
            )

    @property
    def images(self) -> list[ImageResource]:
        # Resolve after parsing so a base element also applies to earlier images.
        return [replace(image, url=urljoin(self._base_url, image.url)) for image in self._images]

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False
            value = "".join(self._title).strip()
            if value:
                self.title = value

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title.append(data)


class GenericHtmlPlugin:
    """Built-in fallback for ordinary public HTML galleries."""

    def validate_config(self, config, app_settings) -> None:
        # The static built-in manifest is authoritative and this fallback has
        # no private user configuration.
        return None

    def matches(self, url: str) -> bool:
        return urlparse(url).scheme in {"http", "https"}

    async def inspect(self, url: str, context: PluginExecutionContext) -> DownloadManifest:
        return await self._load_manifest(url, context)

    async def _load_manifest(self, url: str, context: PluginExecutionContext) -> DownloadManifest:
        response = await context.requests.execute(RequestSpec(url))
        parser = _ImageParser(response.url)
        # Independent plugins, including subclasses, retain their own legacy policy.
        html = (
            _decode_html(response.body, response.headers)
            if type(self) is GenericHtmlPlugin
            else response.body.decode("utf-8", errors="replace")
        )
        parser.feed(html)
        parser.close()
        images = parser.images
        if type(self) is GenericHtmlPlugin:
            images = [replace(image, referer=_automatic_referer(response.url, image.url)) for image in images]
        return DownloadManifest(parser.title, (Chapter(1, parser.title, images=tuple(images)),))

    async def check_updates(self, url: str, context: PluginExecutionContext) -> UpdateSnapshot:
        manifest = await self._load_manifest(url, context)
        images = [
            {"url": image.url, "index": image.index, "image_id": image.image_id}
            for chapter in manifest.chapters
            for image in chapter.images
        ]
        canonical = json.dumps(images, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        revision = "generic-html-image-list-v1:" + sha256(canonical.encode("utf-8")).hexdigest()
        return UpdateSnapshot(url, (UpdateCandidate(url, revision=revision),), datetime.now(UTC))

    async def create_image_request(self, image: ImageResource, context: PluginExecutionContext) -> RequestSpec:
        return RequestSpec(image.url, headers=image.headers, referer=image.referer)

    async def recover_image_request(
        self, image: ImageResource, failed: RequestSpec, response: RequestResponse, context: PluginExecutionContext
    ) -> RequestSpec | None:
        return None

    def auth_flow(self, context: PluginExecutionContext):
        return None

    async def transform_image(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact:
        return artifact
