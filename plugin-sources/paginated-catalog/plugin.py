"""Example plugin for a paginated HTML catalog with per-work detail pages."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from image_downloader import (
    Chapter,
    DownloadManifest,
    ImageArtifact,
    ImageResource,
    PluginExecutionContext,
    RequestResponse,
    RequestSpec,
    TransformContext,
)

CATALOG_HOST = "catalog.example.test"


def _attributes(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
    return {name.lower(): value or "" for name, value in attrs}


class _ListingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.work_links: list[str] = []
        self.next_link: str | None = None
        self.title = ""
        self._title_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = _attributes(attrs)
        if tag.lower() == "a" and values.get("href"):
            if "data-work-link" in values:
                self.work_links.append(values["href"])
            if "next" in values.get("rel", "").lower().split():
                self.next_link = values["href"]
        if tag.lower() == "h1" and "collection-title" in values.get("class", "").split():
            self._title_depth = 1
        elif self._title_depth:
            self._title_depth += 1

    def handle_endtag(self, tag: str) -> None:
        del tag
        if self._title_depth:
            self._title_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._title_depth:
            self.title += data


@dataclass
class _ParsedChapter:
    chapter_id: str
    title: str
    images: list[tuple[str, str | None]] = field(default_factory=list)


class _DetailParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.work_id: str | None = None
        self.work_title = ""
        self.chapters: list[_ParsedChapter] = []
        self._active: _ParsedChapter | None = None
        self._section_depth = 0
        self._work_title_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = _attributes(attrs)
        name = tag.lower()
        if name == "article" and values.get("data-work-id"):
            self.work_id = values["data-work-id"]
        if name == "h1" and "work-title" in values.get("class", "").split():
            self._work_title_depth = 1
        elif self._work_title_depth:
            self._work_title_depth += 1
        if name == "section" and values.get("data-chapter-id"):
            self._active = _ParsedChapter(values["data-chapter-id"], values.get("data-chapter-title", ""))
            self.chapters.append(self._active)
            self._section_depth = 1
        elif self._section_depth:
            self._section_depth += 1
        if name == "img" and self._active is not None and values.get("src"):
            self._active.images.append((values["src"], values.get("data-image-id") or None))

    def handle_endtag(self, tag: str) -> None:
        del tag
        if self._work_title_depth:
            self._work_title_depth -= 1
        if self._section_depth:
            self._section_depth -= 1
            if not self._section_depth:
                self._active = None

    def handle_data(self, data: str) -> None:
        if self._work_title_depth:
            self.work_title += data


class PaginatedCatalogSitePlugin:
    """Concrete fixture-site example: listing pages become ordered chapters."""

    def validate_config(self, config: Mapping[str, object], app_settings: Mapping[str, object]) -> None:
        del app_settings
        if set(config) != {"max_pages"}:
            raise ValueError("paginated catalog config must contain only max_pages")
        value = config.get("max_pages")
        if type(value) is not int or not 1 <= value <= 50:
            raise ValueError("paginated catalog max_pages must be an integer from 1 to 50")

    def matches(self, url: str) -> bool:
        parsed = urlparse(url)
        return parsed.scheme in {"http", "https"} and parsed.hostname == CATALOG_HOST and parsed.path.startswith("/collections/")

    async def inspect(self, url: str, context: PluginExecutionContext) -> DownloadManifest:
        max_pages = int(context.config["max_pages"])
        work_urls: list[str] = []
        seen_works: set[str] = set()
        seen_pages: set[str] = set()
        current = url
        collection_title = ""

        for _ in range(max_pages):
            if current in seen_pages:
                break
            seen_pages.add(current)
            response = await context.requests.execute(RequestSpec(current, headers={"Accept": "text/html"}))
            listing = _ListingParser()
            listing.feed(response.body.decode("utf-8", errors="replace"))
            collection_title = collection_title or listing.title.strip()
            for href in listing.work_links:
                resolved = urljoin(current, href)
                if resolved not in seen_works:
                    seen_works.add(resolved)
                    work_urls.append(resolved)
            next_page = urljoin(current, listing.next_link) if listing.next_link else None
            if not next_page or next_page in seen_pages:
                break
            current = next_page
        else:
            raise ValueError("paginated catalog exceeded max_pages")

        chapters: list[Chapter] = []
        for work_url in work_urls:
            response = await context.requests.execute(RequestSpec(work_url, headers={"Accept": "text/html"}))
            detail = _DetailParser()
            detail.feed(response.body.decode("utf-8", errors="replace"))
            if not detail.work_id or not detail.chapters:
                raise ValueError("catalog detail page is missing work or chapter data")
            for parsed in detail.chapters:
                images = tuple(
                    ImageResource(urljoin(work_url, source), index=index, referer=work_url, image_id=image_id)
                    for index, (source, image_id) in enumerate(parsed.images, start=1)
                )
                title = parsed.title.strip() or detail.work_title.strip() or detail.work_id
                chapters.append(Chapter(len(chapters) + 1, title, images=images, chapter_id=f"{detail.work_id}:{parsed.chapter_id}"))
        title = collection_title or "Catalog collection"
        return DownloadManifest(title, tuple(chapters), metadata={"pattern": "paginated-html-catalog"})

    async def create_image_request(self, image: ImageResource, context: PluginExecutionContext) -> RequestSpec:
        del context
        return RequestSpec(image.url, headers={"Accept": "image/*"}, referer=image.referer)

    async def recover_image_request(
        self, image: ImageResource, failed: RequestSpec, response: RequestResponse, context: PluginExecutionContext
    ) -> RequestSpec | None:
        del image, failed, response, context
        return None

    def auth_flow(self, context: PluginExecutionContext):
        del context
        return None

    async def transform_image(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact:
        del context
        return artifact
