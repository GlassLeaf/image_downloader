"""Site plugin for C."""

from __future__ import annotations

import dataclasses
import io
import json
import re
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from PIL import Image

from image_downloader import (
    AuthContext,
    AuthenticationError,
    AuthFlow,
    AuthLease,
    AuthMode,
    AuthStatus,
    AuthVerification,
    BaseAuthFlow,
    Chapter,
    DownloadManifest,
    HttpStatusError,
    ImageArtifact,
    ImageResource,
    PluginExecutionContext,
    RequestResponse,
    RequestSpec,
    TransformContext,
)
from image_downloader.exceptions import PluginError


def _user_id(value: object) -> int:
    if value is None or value == "":
        return 0
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("invalid user ID")
    user_id = int(value)
    if user_id < 0:
        raise ValueError("invalid user ID")
    return user_id


class LoginFlow(BaseAuthFlow):
    def __init__(self, host: str) -> None:
        self.host = host
        self.csrfApiPath = "/api/auth/csrf"
        self.loginPath = "/api/auth/callback/credentials"
        self.sessionPath = "/api/auth/session"

    async def initialize(self, auth_context: AuthContext) -> AuthLease:
        return AuthLease()

    async def _login(self, auth_context: AuthContext) -> bool:
        response = await auth_context.requests.execute(RequestSpec(self.host + self.csrfApiPath, auth=AuthMode.NONE))
        if not 200 <= response.status < 300:
            return False
        try:
            token_result = json.loads(response.body)
        except ValueError:
            return False
        token = token_result.get("csrfToken") if isinstance(token_result, dict) else None
        if not isinstance(token, str) or not token:
            return False
        response = await auth_context.requests.execute(
            RequestSpec(
                self.host + self.loginPath,
                method="POST",
                json={
                    "id": auth_context.credentials.get("id"),
                    "password": auth_context.credentials.get("password"),
                    "csrfToken": token,
                    "json": True,
                    "redirect": False,
                },
                auth=AuthMode.NONE,
            )
        )
        return 200 <= response.status < 300

    async def verify(self, auth_context: AuthContext) -> AuthVerification:
        response = await auth_context.requests.execute(RequestSpec(self.host + self.sessionPath, auth=AuthMode.NONE))
        if not 200 <= response.status < 300:
            return AuthVerification(AuthStatus.INVALID)
        try:
            session = json.loads(response.body)
        except ValueError as exc:
            raise AuthenticationError("session response did not contain valid JSON") from exc
        if not isinstance(session, dict):
            return AuthVerification(AuthStatus.INVALID)
        try:
            user_id = _user_id(session.get("user"))
            expires = datetime.fromisoformat(session.get("expires"))
        except (TypeError, ValueError):
            return AuthVerification(AuthStatus.INVALID)
        valid = (
            user_id > 0
            and bool(session.get("idToken"))
            and expires.utcoffset() is not None
            and expires > datetime.now(UTC)
        )
        return AuthVerification(AuthStatus.VALID if valid else AuthStatus.INVALID)

    async def login(self, auth_context: AuthContext) -> AuthLease:
        if not await self._login(auth_context) or (await self.verify(auth_context)).status is not AuthStatus.VALID:
            raise AuthenticationError("login session request failed")
        return AuthLease()


class CDownloadPlugin:
    def __init__(self) -> None:
        self.host = ""
        self.apiPath = "/api"
        self.viewerId = ""
        self.userId = 0

    def validate_config(self, config: Mapping[str, object], app_settings: Mapping[str, object]) -> None:
        hosts = config.get("supported_hosts", ())
        if not isinstance(hosts, (list, tuple)) or any(not isinstance(host, str) or not host for host in hosts):
            raise ValueError("supported_hosts must be a list of non-empty host names")

    def matches_with_config(self, url: str, config: Mapping[str, object], app_settings: Mapping[str, object]) -> bool:
        hosts = config.get("supported_hosts", ())
        try:
            parsed = urlsplit(url)
            return (
                parsed.scheme in {"http", "https"}
                and parsed.hostname is not None
                and any(parsed.hostname.lower() == host.lower() for host in hosts)
            )
        except ValueError:
            return False

    def matches(self, url: str) -> bool:
        # Selection uses matches_with_config; there is no separate hard-coded host list.
        return False

    async def get_params(self, url: str, context: PluginExecutionContext) -> BeautifulSoup:
        try:
            parsed = urlsplit(url)
            episode = re.search(r"/episodes/([0-9a-z]+)(?:/|$)", parsed.path)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or episode is None:
                raise ValueError("invalid episode URL")
        except ValueError as exc:
            raise PluginError("invalid episode URL") from exc
        eid = episode.group(1)
        self.host = f"{parsed.scheme}://{parsed.netloc}"
        self.apiPath = "/api"
        self.viewerId = ""
        self.userId = 0

        response = await context.requests.execute(RequestSpec(url))
        soup = BeautifulSoup(response.body, "html.parser")
        api_domain = soup.select_one("[data-api-domain]")
        if api_domain is not None:
            # The API remains on the page's origin; only its path is configurable.
            path = urlsplit(api_domain["data-api-domain"]).path.strip("/")
            self.apiPath = f"/{path}" if path else ""

        for attribute in ("cms-viewer-id", "data-cms-viewer-id"):
            viewer = soup.select_one(f"[{attribute}]")
            if viewer is not None and viewer.get(attribute):
                self.viewerId = str(viewer[attribute])
                break
        else:
            response = await context.requests.execute(RequestSpec(f"{self.host}/api/episodes/{eid}"))
            try:
                info = json.loads(response.body)
                viewer_id = info["episode"]["content"][0]["viewerId"]
                if viewer_id is None or viewer_id == "":
                    raise ValueError("missing viewerId")
                self.viewerId = str(viewer_id)
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise PluginError("episode response did not contain a viewerId") from exc

        user = soup.select_one("#login_user_id")
        try:
            if user is not None and user.get_text(strip=True):
                self.userId = _user_id(user.get_text(strip=True))
            else:
                response = await context.requests.execute(RequestSpec(f"{self.host}/api/auth/session"))
                self.userId = _user_id(json.loads(response.body).get("user"))
        except (AttributeError, TypeError, ValueError) as exc:
            raise PluginError("page or session response did not contain a valid user ID") from exc
        return soup

    async def get_image_infos(self, url: str, context: PluginExecutionContext) -> list[dict[str, Any]]:
        payload = {
            "user-id": str(self.userId),
            "cms-viewer-id": self.viewerId,
            "page-from": "0",
            "page-to": "1",
        }
        headers = {"Referer": url}
        api_url = self.host + self.apiPath + "/book/contentsInfo"
        response = await context.requests.execute(RequestSpec(api_url, query=payload, headers=headers))
        try:
            payload["page-to"] = str(json.loads(response.body)["totalPages"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PluginError("contentsInfo response did not contain totalPages") from exc
        response = await context.requests.execute(RequestSpec(api_url, query=payload, headers=headers))
        try:
            return [
                {
                    "num": info["sort"],
                    "imageUrl": info["imageUrl"],
                    "table": json.loads(info["scramble"]),
                }
                for info in json.loads(response.body)["result"]
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise PluginError("contentsInfo response did not contain valid image data") from exc

    @staticmethod
    async def _legacy_metadata(request: RequestSpec, context: PluginExecutionContext) -> Any:
        try:
            response = await context.requests.execute(replace(request, auth=AuthMode.NONE))
        except HttpStatusError as exc:
            if exc.status != 404:
                raise
            return None
        return json.loads(response.body) if response.status < 300 else None

    async def inspect(self, url: str, context: PluginExecutionContext) -> DownloadManifest:
        content_title = ""
        chapter_title = ""
        soup = await self.get_params(url, context)
        if self.userId == 0 and await context.auth.request_login("Load account-specific image access"):
            response = await context.requests.execute(RequestSpec(f"{self.host}/api/auth/session"))
            try:
                self.userId = _user_id(json.loads(response.body).get("user"))
            except (AttributeError, TypeError, ValueError) as exc:
                raise PluginError("session response did not contain a valid user ID") from exc
        title = soup.select_one('meta[property="og:title"]')
        if title is not None:
            content_title = str(title.get("content", ""))
        elif (title := soup.select_one("title")) is not None:
            content_title = title.get_text(strip=True)
        if (title := soup.select_one('[class*="main-h-text"] a')) is not None:
            content_title = title.get_text(strip=True)
        if (title := soup.select_one('span[class*="main-h-text"]')) is not None:
            chapter_title = title.get_text(strip=True)
            for string in title.stripped_strings:
                if content_title not in string:
                    chapter_title = string

        payload = {"cms-viewer-id": self.viewerId}
        headers = {"Referer": url}
        info = await self._legacy_metadata(
            RequestSpec(self.host + self.apiPath + "/book/Info", query=payload, headers=headers), context
        )
        if info is not None and info.get("result") and info["result"].get("title"):
            content_title = info["result"]["title"]
        info = await self._legacy_metadata(
            RequestSpec(
                self.host + self.apiPath + "/book/episodeInfo",
                query={**payload, "isPreview": "false"},
                headers=headers,
            ),
            context,
        )
        if info is not None:
            for episode in info.get("result") or ():
                if self.viewerId == str(episode["id"]):
                    if episode["name"] not in content_title:
                        chapter_title = episode["name"]
                    break
        image_infos = await self.get_image_infos(url, context)
        images = tuple(
            ImageResource(
                url=info["imageUrl"],
                index=int(info["num"]) + 1,
                plugin_data={"table": info["table"]},
                referer=self.host,
            )
            for info in image_infos
        )
        return DownloadManifest(title=content_title, chapters=(Chapter(1, title=chapter_title, images=images),))

    async def create_image_request(self, image: ImageResource, context: PluginExecutionContext) -> RequestSpec:
        return RequestSpec(image.url, referer=image.referer)

    async def recover_image_request(
        self,
        image: ImageResource,
        failed: RequestSpec,
        response: RequestResponse,
        context: PluginExecutionContext,
    ) -> RequestSpec | None:
        return None

    def auth_flow(self, context: PluginExecutionContext) -> AuthFlow | None:
        return LoginFlow(context.endpoints["operation"].origin)

    def transform_image(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact:
        table = context.plugin_data.image.get("table")
        if (
            not isinstance(table, (list, tuple))
            or len(table) != 16
            or any(isinstance(item, bool) or not isinstance(item, int) or not 0 <= item < 16 for item in table)
        ):
            raise PluginError("scramble table must contain 16 integers in the range 0..15")

        with Image.open(io.BytesIO(artifact.data)) as original, original.copy() as descrambled, io.BytesIO() as output:
            block_width = original.width // 4
            block_height = original.height // 4
            # Both source and destination indices traverse columns before rows.
            for destination, source in enumerate(table):
                xsrc = block_width * (source // 4)
                ysrc = block_height * (source % 4)
                xdst = block_width * (destination // 4)
                ydst = block_height * (destination % 4)
                with original.crop((xsrc, ysrc, xsrc + block_width, ysrc + block_height)) as block:
                    descrambled.paste(block, (xdst, ydst))
            descrambled.save(output, format="webp", lossless=True)
            data = output.getvalue()
        return dataclasses.replace(
            artifact,
            data=data,
            content_type="image/webp",
            extension=".webp",
            history=(*artifact.history, "plugin-c.descramble"),
        )
