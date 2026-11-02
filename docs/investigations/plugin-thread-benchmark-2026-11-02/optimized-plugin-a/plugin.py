"""Site plugin for A."""

from __future__ import annotations

import dataclasses
import io
import json
import re
import threading
from collections import OrderedDict
from collections.abc import Mapping
from contextlib import ExitStack
from typing import Any
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from PIL import Image

from image_downloader import (
    AuthFlow,
    Chapter,
    DownloadManifest,
    ImageArtifact,
    ImageResource,
    PluginExecutionContext,
    RequestResponse,
    RequestSpec,
    TransformContext,
)
from image_downloader.exceptions import ImageDimensionLimitError, PluginError

from .wat_module import build_transform_table, decode_placeholder

# Replace these exact host names when adapting the template. Subdomains are
# intentionally excluded unless explicitly listed here.
SUPPORTED_HOSTS = frozenset({"example.com", "www.example.com"})


class ADownloadPlugin:
    domain: str = "https://www.example.com"
    api_path: str = "/manga/official/viewer.json"

    _CACHE_ENTRIES = 4
    _CACHE_BYTES = 16 * 1024 * 1024

    def __init__(self):
        self._cache_lock = threading.Lock()
        self._placeholder_cache = OrderedDict()
        self._cached_bytes = 0

    def _records(self, chapter_id, placeholder):
        key = (chapter_id, placeholder)
        with self._cache_lock:
            cached = self._placeholder_cache.get(key)
            if cached is not None:
                self._placeholder_cache.move_to_end(key)
                return cached
            groups = decode_placeholder(placeholder)
            size = sum(len(group) for group in groups if group is not None)
            if size <= self._CACHE_BYTES:
                while self._placeholder_cache and (
                    len(self._placeholder_cache) >= self._CACHE_ENTRIES or self._cached_bytes + size > self._CACHE_BYTES
                ):
                    _, evicted = self._placeholder_cache.popitem(last=False)
                    self._cached_bytes -= sum(len(group) for group in evicted if group is not None)
                self._placeholder_cache[key] = groups
                self._cached_bytes += size
            return groups

    def cleanup_after_use(self):
        with self._cache_lock:
            self._placeholder_cache.clear()
            self._cached_bytes = 0

    def validate_config(self, config: Mapping[str, object], app_settings: Mapping[str, object]) -> None:
        return None

    def matches(self, url: str) -> bool:
        try:
            parsed = urlsplit(url)
            return parsed.scheme in {"http", "https"} and parsed.hostname in SUPPORTED_HOSTS
        except ValueError:
            return False

    async def get_chapter_infos(self, url: str, context: PluginExecutionContext) -> list[dict[str, Any]]:
        import logging

        chapter_infos = []
        m = re.search("/manga/([^/]+)/(\d+)(/(\d+)/?)?", url)
        if m and m[1] and m[2]:
            tag = m[1]
            workId = m[2]
            response = await context.requests.execute(RequestSpec(f"{ADownloadPlugin.domain}/manga/{tag}/{workId}/"))
            soup = BeautifulSoup(response.body, "html.parser")
            try:
                chapter_infos = json.loads(
                    soup.select('#app-official-manga-toc script[type="application/json"]')[0].text
                )["episodes"]
                if m[4]:
                    found = False
                    for chapter in chapter_infos:
                        if str(chapter["episodeNo"]) == m[4]:
                            found = True
                            chapter_infos = [chapter]
                            break
                    if not found:
                        chapter_infos = []
            except Exception:
                logging.getLogger(__name__).info("no chapter found")
        return chapter_infos

    async def get_chapter_page_info(self, url: str, context: PluginExecutionContext) -> dict[str, Any]:
        m = re.search("/manga/[^/]+/(\d+)(/(\d+)/?)?", url)
        workId = m[1]
        chapterId = m[3]
        response = await context.requests.execute(RequestSpec(url))
        cookies = response.set_cookies()
        xsrf_token = cookies.get("XSRF-TOKEN")
        infos = []
        if xsrf_token and chapterId and workId:
            xsrf_token = xsrf_token.replace("%3D", "=")
            post_params = {
                "episode_no": int(chapterId),
                "manga_sele_id": int(workId),
                "preview": False,
                "hide_page": False,
                "resolution": "full_hd",  # 'standard' or 'full_hd'
            }
            headers = {"X-Requested-With": "XMLHttpRequest", "X-XSRF-TOKEN": xsrf_token}
            response = await context.requests.execute(
                RequestSpec(
                    method="POST",
                    url=ADownloadPlugin.domain + ADownloadPlugin.api_path,
                    json=post_params,
                    headers=headers,
                )
            )
            try:
                chapter_info = json.loads(response.body)
                infos = {
                    "title": chapter_info["manga"]["title"],
                    "subtitle": chapter_info["episode"]["title"],
                    "page": chapter_info["page"],
                }
            except Exception:
                pass
        return infos

    async def inspect(self, url: str, context: PluginExecutionContext) -> DownloadManifest:
        await context.requests.execute(RequestSpec(url))
        chapter_infos = await self.get_chapter_infos(url, context)
        chapters = []
        chapter_num = 1
        content_title = ""
        for chapter_info in chapter_infos:
            if chapter_info["rental"]["isFree"] or chapter_info["rental"]["isOnRental"]:
                page_info = await self.get_chapter_page_info(ADownloadPlugin.domain + chapter_info["url"], context)
                import logging

                logging.getLogger(__name__).info(
                    "chapter=%s images=%s", chapter_info["episodeNo"], len(page_info["page"]["images"])
                )
                content_title = page_info["title"]
                images = []
                image_index = 1
                for info in page_info["page"]["images"]:
                    images.append(
                        ImageResource(
                            info["url"],
                            index=image_index,
                            metadata={
                                "iv": info.get("iv") or "",
                                "salt": info.get("salt") or "",
                                "placeholder": page_info["page"]["placeholder"],
                            },
                        )
                    )
                    image_index += 1
                chapters.append(
                    Chapter(
                        chapter_num,
                        chapter_id=str(chapter_info["episodeNo"]),
                        title=page_info["subtitle"],
                        images=tuple(images),
                    )
                )
                chapter_num += 1
        return DownloadManifest(title=content_title, chapters=tuple(chapters))

    async def create_image_request(self, image: ImageResource, context: PluginExecutionContext) -> RequestSpec:
        return RequestSpec(image.url)

    async def recover_image_request(
        self,
        image: ImageResource,
        failed: RequestSpec,
        response: RequestResponse,
        context: PluginExecutionContext,
    ) -> RequestSpec | None:
        return None

    def auth_flow(self, context: PluginExecutionContext) -> AuthFlow | None:
        return None

    def transform_image(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact:
        iv = context.image_metadata["iv"]
        salt = context.image_metadata["salt"]
        if not (iv or salt):
            return artifact
        try:
            groups = self._records(
                context.chapter.chapter_id or str(context.chapter.number), context.image_metadata["placeholder"]
            )
            if not 1 <= context.index <= len(groups) or groups[context.index - 1] is None:
                raise ValueError("image index has no transform records")
            records = groups[context.index - 1]
            with ExitStack() as resources:
                original = resources.enter_context(Image.open(io.BytesIO(artifact.data)))
                limit = context.app_settings.get("media", {}).get("max_image_pixels")
                if limit is not None and original.width * original.height > limit:
                    raise ImageDimensionLimitError("image exceeds configured pixel limit")
                tableArray = build_transform_table(records, original.width, original.height, iv, salt)
                originalWidth, originalHeight, adjust, coeff, margin = tableArray[:5]
                descrambled = resources.enter_context(original.copy())

                def decode_data(x, adj, coef):
                    return (x - adj) // coef

                data = {
                    tableArray[i + 8]: {
                        "dx": decode_data(tableArray[i], adjust, coeff),
                        "dy": decode_data(tableArray[i + 1], adjust, coeff),
                        "sx": decode_data(tableArray[i + 2], adjust, coeff),
                        "sy": decode_data(tableArray[i + 3], adjust, coeff),
                        "rotation": decode_data(tableArray[i + 4], adjust, coeff),
                        "xScaling": 1 - decode_data(tableArray[i + 5], adjust, coeff) * 2,
                        "cellWidth": tableArray[i + 6],
                        "cellHeight": tableArray[i + 7],
                    }
                    for i in range(5, len(tableArray), 9)
                }
                for _, mappingInfo in sorted(data.items(), reverse=True):
                    with ExitStack() as tiles:
                        cropped = tiles.enter_context(
                            original.crop(
                                (
                                    mappingInfo["sx"] + margin,
                                    mappingInfo["sy"] + margin,
                                    mappingInfo["sx"] + mappingInfo["cellWidth"] - margin,
                                    mappingInfo["sy"] + mappingInfo["cellHeight"] - margin,
                                )
                            )
                        )
                        if mappingInfo["xScaling"] == -1:
                            cropped = tiles.enter_context(cropped.transpose(Image.Transpose.FLIP_LEFT_RIGHT))
                        rotation = mappingInfo["rotation"]
                        if rotation in (1, 2, 3):
                            cropped = tiles.enter_context(
                                cropped.transpose(
                                    {
                                        1: Image.Transpose.ROTATE_90,
                                        2: Image.Transpose.ROTATE_180,
                                        3: Image.Transpose.ROTATE_270,
                                    }[rotation]
                                )
                            )
                        descrambled.paste(cropped, (mappingInfo["dx"] + margin, mappingInfo["dy"] + margin))
                final = resources.enter_context(descrambled.crop((0, 0, originalWidth, originalHeight)))
                with io.BytesIO() as output:
                    final.save(output, format="webp", lossless=True)
                    return dataclasses.replace(
                        artifact,
                        data=output.getvalue(),
                        content_type="image/webp",
                        extension=".webp",
                        history=(*artifact.history, "plugin-a.descramble"),
                    )
        except ValueError as exc:
            raise PluginError("invalid plugin-a transform metadata") from exc
