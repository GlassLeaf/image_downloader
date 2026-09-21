"""Typed, exception-safe invocation boundary for plugin callbacks."""

from __future__ import annotations

import inspect
import re
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from typing import TypeVar
from urllib.parse import urlparse

from ..exceptions import ImageDownloaderError, PluginError
from ..models import (
    Chapter,
    DownloadManifest,
    ImageArtifact,
    ImageResource,
    ImageSaveOptions,
    RequestResponse,
    RequestSpec,
    UpdateCandidate,
    UpdateSnapshot,
)
from ..observability.logging import DownloadLogger
from ..ports import (
    AuthFlow,
    ConfigurableSitePlugin,
    ImageProcessor,
    PluginExecutionContext,
    SitePlugin,
    TransformContext,
    UpdateProvider,
    is_auth_flow,
)

_T = TypeVar("_T")
_HTTP_METHOD = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")


class PluginInvoker:
    """Own callback exception conversion, result validation, and hook logging."""

    def __init__(self, plugin_id: str, logger: DownloadLogger | None = None) -> None:
        self.plugin_id = plugin_id
        self.logger = logger

    def _error(self, hook: str, message: str) -> PluginError:
        return PluginError(f"{message} (plugin={self.plugin_id}, hook={hook})")

    async def _start(
        self,
        hook: str,
        *,
        module: str = "plugin",
        url: str | None = None,
        chapter_id: str | None = None,
    ) -> None:
        if self.logger is not None:
            await self.logger.core(
                "plugin_call_started",
                module=module,
                chapter_id=chapter_id,
                url=url,
                plugin_id=self.plugin_id,
                action=hook,
                debug=True,
            )

    async def _finish(
        self,
        hook: str,
        *,
        module: str = "plugin",
        url: str | None = None,
        chapter_id: str | None = None,
        bytes_count: int | None = None,
    ) -> None:
        if self.logger is not None:
            await self.logger.core(
                "plugin_call_finished",
                module=module,
                chapter_id=chapter_id,
                url=url,
                bytes_count=bytes_count,
                plugin_id=self.plugin_id,
                action=hook,
                debug=True,
            )

    async def _invoke_async(self, hook: str, callback: Callable[[], Awaitable[_T]]) -> _T:
        try:
            return await callback()
        except ImageDownloaderError:
            raise
        except Exception as exc:
            raise self._error(hook, "plugin hook failed") from exc

    def _invoke_sync(self, hook: str, callback: Callable[[], _T]) -> _T:
        try:
            return callback()
        except ImageDownloaderError:
            raise
        except Exception as exc:
            raise self._error(hook, "plugin hook failed") from exc

    def validate_config(
        self,
        plugin: SitePlugin | ImageProcessor,
        config: Mapping[str, object],
        app_settings: Mapping[str, object],
    ) -> None:
        result = self._invoke_sync("validate_config", lambda: plugin.validate_config(config, app_settings))
        if result is not None:
            raise self._error("validate_config", "validate_config must return None")

    def matches(
        self,
        plugin: SitePlugin,
        url: str,
        *,
        config: Mapping[str, object],
        app_settings: Mapping[str, object],
    ) -> tuple[bool, str]:
        if isinstance(plugin, ConfigurableSitePlugin):
            hook = "matches_with_config"
            matched = self._invoke_sync(hook, lambda: plugin.matches_with_config(url, config, app_settings))
            matcher = "configured"
        else:
            hook = "matches"
            matched = self._invoke_sync(hook, lambda: plugin.matches(url))
            matcher = "matches"
        if not isinstance(matched, bool):
            raise self._error(hook, "plugin match hook must return bool")
        return matched, matcher

    async def inspect(
        self,
        plugin: SitePlugin,
        url: str,
        context: PluginExecutionContext,
    ) -> DownloadManifest:
        hook = "inspect"
        await self._start(hook, url=url)
        value = await self._invoke_async(hook, lambda: plugin.inspect(url, context))
        self._validate_manifest(hook, value)
        await self._finish(hook, url=url)
        return value

    async def check_updates(
        self,
        plugin: UpdateProvider,
        url: str,
        context: PluginExecutionContext,
    ) -> UpdateSnapshot:
        hook = "check_updates"
        await self._start(hook, url=url)
        value = await self._invoke_async(hook, lambda: plugin.check_updates(url, context))
        self._validate_update_snapshot(hook, value)
        await self._finish(hook, url=url)
        return value

    def auth_flow(self, plugin: SitePlugin, context: PluginExecutionContext) -> AuthFlow | None:
        hook = "auth_flow"
        value = self._invoke_sync(hook, lambda: plugin.auth_flow(context))
        if value is not None and not is_auth_flow(value):
            raise self._error(hook, "auth_flow must return AuthFlow or None")
        return value

    async def create_image_request(
        self,
        plugin: SitePlugin,
        image: ImageResource,
        context: PluginExecutionContext,
    ) -> RequestSpec:
        hook = "create_image_request"
        await self._start(hook, url=image.url)
        value = await self._invoke_async(hook, lambda: plugin.create_image_request(image, context))
        self._validate_request(hook, value, allow_none=False)
        assert isinstance(value, RequestSpec)
        await self._finish(hook, url=value.url)
        return value

    async def recover_image_request(
        self,
        plugin: SitePlugin,
        image: ImageResource,
        failed: RequestSpec,
        response: RequestResponse,
        context: PluginExecutionContext,
    ) -> RequestSpec | None:
        hook = "recover_image_request"
        await self._start(hook, url=failed.url)
        value = await self._invoke_async(
            hook,
            lambda: plugin.recover_image_request(image, failed, response, context),
        )
        self._validate_request(hook, value, allow_none=True)
        await self._finish(hook, url=value.url if value is not None else failed.url)
        return value

    async def transform_image(
        self,
        plugin: SitePlugin,
        artifact: ImageArtifact,
        context: TransformContext,
        *,
        chapter_id: str,
    ) -> ImageArtifact:
        hook = "transform_image"
        await self._start(hook, url=artifact.source_url, chapter_id=chapter_id)
        value = await self._invoke_async(hook, lambda: plugin.transform_image(artifact, context))
        self._validate_artifact(hook, value)
        await self._finish(
            hook,
            url=value.source_url,
            chapter_id=chapter_id,
            bytes_count=len(value.data),
        )
        return value

    async def transform_processor(
        self,
        processor: ImageProcessor,
        artifact: ImageArtifact,
        context: TransformContext,
        *,
        chapter_id: str,
    ) -> ImageArtifact:
        hook = "transform"
        await self._start(hook, module="processor", url=artifact.source_url, chapter_id=chapter_id)
        value = await self._invoke_async(hook, lambda: processor.transform(artifact, context))
        self._validate_artifact(hook, value)
        await self._finish(
            hook,
            module="processor",
            url=value.source_url,
            chapter_id=chapter_id,
            bytes_count=len(value.data),
        )
        return value

    async def close_processor(self, processor: ImageProcessor) -> None:
        """Invoke an optional synchronous or asynchronous processor cleanup hook."""
        async_close = getattr(processor, "aclose", None)
        sync_close = getattr(processor, "close", None)
        callback = async_close if callable(async_close) else sync_close
        if not callable(callback):
            return
        hook = "aclose" if callback is async_close else "close"
        try:
            result = callback()
            if inspect.isawaitable(result):
                result = await result
        except ImageDownloaderError:
            raise
        except Exception as exc:
            raise self._error(hook, "plugin hook failed") from exc
        if result is not None:
            raise self._error(hook, "processor cleanup must return None")

    async def apply_auth(self, flow: AuthFlow, request: RequestSpec) -> RequestSpec:
        hook = "auth_apply"
        value = await self._invoke_async(hook, lambda: flow.apply(request))
        self._validate_request(hook, value, allow_none=False)
        assert isinstance(value, RequestSpec)
        return value

    async def refresh_auth(
        self,
        flow: AuthFlow,
        failed: RequestSpec,
        response: RequestResponse,
    ) -> RequestSpec | None:
        hook = "auth_refresh"
        value = await self._invoke_async(hook, lambda: flow.refresh(failed, response))
        self._validate_request(hook, value, allow_none=True)
        return value

    def is_auth_failure(self, flow: AuthFlow, request: RequestSpec, response: RequestResponse) -> bool:
        hook = "is_auth_failure"
        value = self._invoke_sync(hook, lambda: flow.is_auth_failure(request, response))
        if not isinstance(value, bool):
            raise self._error(hook, "AuthFlow.is_auth_failure must return bool")
        return value

    def _validate_manifest(self, hook: str, value: object) -> None:
        if not isinstance(value, DownloadManifest):
            raise self._error(hook, "inspect must return DownloadManifest")
        if not isinstance(value.title, str):
            raise self._error(hook, "inspect returned an invalid title")
        optional_text = (value.content_id, value.author, value.access, value.revision)
        if any(item is not None and not isinstance(item, str) for item in optional_text):
            raise self._error(hook, "inspect returned invalid manifest metadata")
        if any(not isinstance(key, str) or not isinstance(item, str) for key, item in value.metadata.items()):
            raise self._error(hook, "inspect returned invalid manifest metadata")
        for chapter_index, chapter in enumerate(value.chapters):
            if not isinstance(chapter, Chapter):
                raise self._error(hook, f"inspect returned an invalid chapter at chapters[{chapter_index}]")
            self._validate_chapter(hook, chapter, chapter_index)

    def _validate_chapter(self, hook: str, chapter: Chapter, chapter_index: int) -> None:
        if isinstance(chapter.number, bool) or not isinstance(chapter.number, int) or chapter.number < 0:
            raise self._error(hook, f"inspect returned an invalid chapter number at chapters[{chapter_index}]")
        if not isinstance(chapter.title, str) or not isinstance(chapter.subtitle, str):
            raise self._error(hook, f"inspect returned invalid chapter text at chapters[{chapter_index}]")
        if chapter.chapter_id is not None and not isinstance(chapter.chapter_id, str):
            raise self._error(hook, f"inspect returned an invalid chapter ID at chapters[{chapter_index}]")
        for image_index, image in enumerate(chapter.images):
            if not isinstance(image, ImageResource):
                path = f"chapters[{chapter_index}].images[{image_index}]"
                raise self._error(hook, f"inspect returned an invalid image at {path}")
            self._validate_image(hook, image, chapter_index, image_index)

    def _validate_image(self, hook: str, image: ImageResource, chapter_index: int, image_index: int) -> None:
        path = f"chapters[{chapter_index}].images[{image_index}]"
        self._validate_http_url(hook, image.url, f"inspect returned an invalid image URL at {path}")
        if isinstance(image.index, bool) or not isinstance(image.index, int) or image.index < 0:
            raise self._error(hook, f"inspect returned an invalid image index at {path}")
        if image.referer is not None:
            self._validate_http_url(hook, image.referer, f"inspect returned an invalid referer at {path}")
        self._validate_string_mapping(hook, image.headers, f"inspect returned invalid image headers at {path}")
        if not isinstance(image.save_options, ImageSaveOptions):
            raise self._error(hook, f"inspect returned invalid save options at {path}")
        self._validate_save_options(hook, image.save_options, path)
        if image.image_id is not None and not isinstance(image.image_id, str):
            raise self._error(hook, f"inspect returned an invalid image ID at {path}")

    def _validate_update_snapshot(self, hook: str, value: object) -> None:
        if not isinstance(value, UpdateSnapshot):
            raise self._error(hook, "check_updates must return UpdateSnapshot")
        self._validate_http_url(hook, value.source_url, "check_updates returned an invalid source URL")
        if not isinstance(value.checked_at, datetime):
            raise self._error(hook, "check_updates returned an invalid checked_at value")
        for index, candidate in enumerate(value.candidates):
            if not isinstance(candidate, UpdateCandidate):
                raise self._error(hook, f"check_updates returned an invalid candidate at candidates[{index}]")
            self._validate_http_url(
                hook,
                candidate.url,
                f"check_updates returned an invalid candidate URL at candidates[{index}]",
            )
            if candidate.content_id is not None and not isinstance(candidate.content_id, str):
                raise self._error(hook, f"check_updates returned an invalid content ID at candidates[{index}]")
            if candidate.revision is not None and not isinstance(candidate.revision, str):
                raise self._error(hook, f"check_updates returned an invalid revision at candidates[{index}]")

    def _validate_request(self, hook: str, value: object, *, allow_none: bool) -> None:
        if value is None and allow_none:
            return
        if not isinstance(value, RequestSpec):
            expected = "RequestSpec or None" if allow_none else "RequestSpec"
            raise self._error(hook, f"{hook} must return {expected}")
        self._validate_http_url(hook, value.url, f"{hook} returned an invalid request URL")
        if not isinstance(value.method, str) or _HTTP_METHOD.fullmatch(value.method) is None:
            raise self._error(hook, f"{hook} returned an invalid HTTP method")
        self._validate_string_mapping(hook, value.headers, f"{hook} returned invalid request headers")
        self._validate_string_mapping(hook, value.cookies, f"{hook} returned invalid request cookies")
        self._validate_string_mapping(hook, value.query, f"{hook} returned invalid request query")
        self._validate_string_mapping(hook, value.form, f"{hook} returned invalid request form")
        if value.referer is not None:
            self._validate_http_url(hook, value.referer, f"{hook} returned an invalid referer")
        if not isinstance(value.auth_required, bool) or not isinstance(value.retry_non_idempotent, bool):
            raise self._error(hook, f"{hook} returned invalid request flags")

    def _validate_artifact(self, hook: str, value: object) -> None:
        if not isinstance(value, ImageArtifact):
            raise self._error(hook, f"{hook} must return ImageArtifact")
        if not isinstance(value.data, bytes) or not isinstance(value.content_type, str):
            raise self._error(hook, f"{hook} returned invalid image data")
        self._validate_http_url(hook, value.source_url, f"{hook} returned an invalid source URL")
        if value.image_id is not None and not isinstance(value.image_id, str):
            raise self._error(hook, f"{hook} returned an invalid image ID")
        if value.extension is not None and not isinstance(value.extension, str):
            raise self._error(hook, f"{hook} returned an invalid extension")
        if any(not isinstance(item, str) for item in value.history):
            raise self._error(hook, f"{hook} returned an invalid history")

    def _validate_save_options(self, hook: str, value: ImageSaveOptions, path: str) -> None:
        if any(item is not None and not isinstance(item, str) for item in (value.format, value.extension)):
            raise self._error(hook, f"inspect returned invalid save options at {path}")
        if any(
            item is not None and (isinstance(item, bool) or not isinstance(item, int))
            for item in (value.quality, value.compress_level)
        ):
            raise self._error(hook, f"inspect returned invalid save options at {path}")
        if any(
            item is not None and not isinstance(item, bool)
            for item in (value.optimize, value.progressive, value.lossless)
        ) or not isinstance(value.exif, bool):
            raise self._error(hook, f"inspect returned invalid save options at {path}")

    def _validate_http_url(self, hook: str, value: object, message: str) -> None:
        if not isinstance(value, str):
            raise self._error(hook, message)
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise self._error(hook, message)

    def _validate_string_mapping(self, hook: str, value: object, message: str) -> None:
        if not isinstance(value, Mapping):
            raise self._error(hook, message)
        if any(not isinstance(key, str) or not isinstance(item, str) for key, item in value.items()):
            raise self._error(hook, message)
