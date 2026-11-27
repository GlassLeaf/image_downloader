"""Typed, exception-safe invocation boundary for plugin callbacks."""

from __future__ import annotations

import inspect
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Literal, TypeVar, cast

from ..exceptions import ImageDownloaderError, PluginError
from ..media.transform_executor import TransformExecutor
from ..models import (
    AdditionalFileHookContext,
    AdditionalFileHookPoint,
    AdditionalFileReceiveResult,
    AdditionalFileSaveResult,
    AuthDecision,
    AuthLease,
    AuthVerification,
    DownloadManifest,
    ImageArtifact,
    ImageFetchRequest,
    ImageResource,
    ImageSaveOptions,
    RequestResponse,
    RequestSpec,
    UpdateSnapshot,
)
from ..observability.logging import DownloadLogger
from ..ports import (
    AuthContext,
    AuthFlow,
    ConfigurableSitePlugin,
    ImageProcessor,
    PluginExecutionContext,
    PluginFormatContext,
    SitePlugin,
    TransformContext,
    UpdateProvider,
    is_auth_flow,
)
from . import plugin_validation as validation

_T = TypeVar("_T")
_OUTPUT_FORMAT_KEY = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")


class PluginInvoker:
    """Own callback exception conversion, result validation, and hook logging."""

    def __init__(self, plugin_id: str, logger: DownloadLogger | None = None) -> None:
        self.plugin_id = plugin_id
        self.logger = logger

    def _error(self, hook: str, message: str) -> PluginError:
        return validation.plugin_error(self.plugin_id, hook, message)

    async def _start(
        self,
        hook: str,
        *,
        module: str = "plugin",
        url: str | None = None,
        chapter_id: str | None = None,
        url_is_locator: bool = False,
    ) -> None:
        if self.logger is not None:
            await self.logger.core(
                "plugin_call_started",
                module=module,
                chapter_id=chapter_id,
                url=url,
                url_is_locator=url_is_locator,
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
        url_is_locator: bool = False,
    ) -> None:
        if self.logger is not None:
            await self.logger.core(
                "plugin_call_finished",
                module=module,
                chapter_id=chapter_id,
                url=url,
                url_is_locator=url_is_locator,
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

    async def _required_async(self, hook: str, callback: Callable[[], object]) -> object:
        async def invoke() -> object:
            value = callback()
            if not inspect.isawaitable(value):
                raise self._error(hook, "additional file hook must be async")
            result: object = await value
            return result

        await self._start(hook)
        result = await self._invoke_async(hook, invoke)
        await self._finish(hook)
        return result

    def additional_file_hook_points(
        self,
        plugin: object,
        context: PluginExecutionContext,
    ) -> tuple[AdditionalFileHookPoint, ...]:
        hook = "additional_file_hook_points"
        declaration = self._invoke_sync(hook, lambda: getattr(plugin, hook, None))
        provider = self._invoke_sync(hook, lambda: getattr(plugin, "additional_files", None))
        if declaration is None and provider is None:
            return ()
        if not callable(declaration) or not callable(provider):
            raise self._error(hook, "additional file methods must be paired callables")
        points = self._invoke_sync(hook, lambda: declaration(context))
        if inspect.isawaitable(points):
            if inspect.iscoroutine(points):
                points.close()
            raise self._error(hook, "additional file declaration must be synchronous")
        if not isinstance(points, tuple) or any(not isinstance(p, AdditionalFileHookPoint) for p in points):
            raise self._error(hook, "invalid additional file hook points")
        if len(set(points)) != len(points):
            raise self._error(hook, "duplicate additional file hook points")
        return cast(tuple[AdditionalFileHookPoint, ...], points)

    async def additional_files(
        self,
        plugin: object,
        hook: AdditionalFileHookContext,
        context: PluginExecutionContext,
    ) -> tuple[object, ...]:
        name = "additional_files"
        value = await self._required_async(name, lambda: getattr(plugin, name)(hook, context))
        if not isinstance(value, tuple):
            raise self._error("additional_files", "additional file hook must return a tuple")
        return value

    async def additional_file_callback(
        self,
        plugin: object,
        name: Literal["additional_file_received", "additional_file_saved"],
        result: AdditionalFileReceiveResult | AdditionalFileSaveResult,
        context: PluginExecutionContext,
    ) -> bool:
        callback = self._invoke_sync(name, lambda: getattr(plugin, name, None))
        if callback is None:
            return False
        if not callable(callback):
            raise self._error(name, "additional file callback must be callable")
        value = await self._required_async(name, lambda: callback(result, context))
        if value is not None:
            raise self._error(name, "additional file callback must return None")
        return True

    def validate_config(
        self,
        plugin: SitePlugin | ImageProcessor,
        config: Mapping[str, object],
        app_settings: Mapping[str, object],
    ) -> None:
        result = self._invoke_sync("validate_config", lambda: plugin.validate_config(config, app_settings))
        if result is not None:
            raise self._error("validate_config", "validate_config must return None")

    def output_format_values(self, plugin: object, context: PluginFormatContext) -> Mapping[str, str]:
        """Invoke an optional stable output-format hook, returning an empty mapping when absent."""
        hook = "output_format_values"
        callback = self._invoke_sync(hook, lambda: getattr(plugin, hook, None))
        if callback is None:
            return {}
        if not callable(callback):
            raise self._error(hook, "output_format_values must be callable")
        value = self._invoke_sync(hook, lambda: callback(context))
        if not isinstance(value, Mapping):
            raise self._error(hook, "output_format_values must return Mapping[str, str]")
        result: dict[str, str] = {}
        for key, item in value.items():
            if not isinstance(key, str) or _OUTPUT_FORMAT_KEY.fullmatch(key) is None:
                raise self._error(hook, "output_format_values returned an invalid key")
            if not isinstance(item, str):
                raise self._error(hook, "output_format_values returned a non-string value")
            result[key] = item
        return result

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
        validation.validate_manifest(self.plugin_id, hook, value)
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
        validation.validate_update_snapshot(self.plugin_id, hook, value)
        await self._finish(hook, url=url)
        return value

    def auth_flow(self, plugin: SitePlugin, context: PluginExecutionContext) -> AuthFlow | None:
        hook = "auth_flow"
        value = self._invoke_sync(hook, lambda: plugin.auth_flow(context))
        if value is not None and not is_auth_flow(value):
            raise self._error(
                hook,
                "auth_flow must return new AuthFlow or None; migrate initialize/apply/classify_response/verify/refresh",
            )
        return value

    async def create_image_request(
        self,
        plugin: SitePlugin,
        image: ImageResource,
        context: PluginExecutionContext,
    ) -> RequestSpec | ImageFetchRequest:
        hook = "create_image_request"
        await self._start(hook, url=image.url, url_is_locator=True)
        value = await self._invoke_async(hook, lambda: plugin.create_image_request(image, context))
        validation.validate_image_fetch_request(self.plugin_id, hook, value, allow_none=False)
        assert isinstance(value, (RequestSpec, ImageFetchRequest))
        request = value.request if isinstance(value, ImageFetchRequest) else value
        await self._finish(hook, url=request.url)
        return value

    async def recover_image_request(
        self,
        plugin: SitePlugin,
        image: ImageResource,
        failed: RequestSpec,
        response: RequestResponse,
        context: PluginExecutionContext,
    ) -> RequestSpec | ImageFetchRequest | None:
        hook = "recover_image_request"
        await self._start(hook, url=failed.url)
        value = await self._invoke_async(
            hook,
            lambda: plugin.recover_image_request(image, failed, response, context),
        )
        validation.validate_image_fetch_request(self.plugin_id, hook, value, allow_none=True)
        request = value.request if isinstance(value, ImageFetchRequest) else value
        await self._finish(hook, url=request.url if request is not None else failed.url)
        return value

    async def transform_image(
        self,
        plugin: SitePlugin,
        artifact: ImageArtifact,
        context: TransformContext,
        *,
        chapter_id: str,
        executor: TransformExecutor,
    ) -> ImageArtifact:
        hook = "transform_image"
        await self._start(hook, url=artifact.source_url, chapter_id=chapter_id, url_is_locator=True)
        value = await executor.run(
            lambda: self._invoke_transform(hook, lambda: plugin.transform_image(artifact, context))
        )
        validation.validate_artifact(self.plugin_id, hook, value)
        await self._finish(
            hook,
            url=value.source_url,
            chapter_id=chapter_id,
            bytes_count=len(value.data),
            url_is_locator=True,
        )
        return value

    async def transform_processor(
        self,
        processor: ImageProcessor,
        artifact: ImageArtifact,
        context: TransformContext,
        *,
        chapter_id: str,
        executor: TransformExecutor,
    ) -> ImageArtifact:
        hook = "transform"
        await self._start(
            hook,
            module="processor",
            url=artifact.source_url,
            chapter_id=chapter_id,
            url_is_locator=True,
        )
        value = await executor.run(lambda: self._invoke_transform(hook, lambda: processor.transform(artifact, context)))
        validation.validate_artifact(self.plugin_id, hook, value)
        await self._finish(
            hook,
            module="processor",
            url=value.source_url,
            chapter_id=chapter_id,
            bytes_count=len(value.data),
            url_is_locator=True,
        )
        return value

    async def image_save_options(
        self,
        plugin: object,
        artifact: ImageArtifact,
        context: TransformContext,
        *,
        chapter_id: str,
        executor: TransformExecutor,
    ) -> ImageSaveOptions | None:
        hook = "image_save_options"
        callback = self._invoke_sync(hook, lambda: getattr(plugin, hook, None))
        if callback is None:
            return None
        if not callable(callback):
            raise self._error(hook, "image_save_options must be callable")
        await self._start(hook, url=artifact.source_url, chapter_id=chapter_id, url_is_locator=True)
        value = await executor.run(lambda: self._invoke_transform(hook, lambda: callback(artifact, context)))
        validation.validate_save_options(self.plugin_id, hook, value)
        await self._finish(hook, url=artifact.source_url, chapter_id=chapter_id, url_is_locator=True)
        return cast(ImageSaveOptions | None, value)

    def _invoke_transform(self, hook: str, callback: Callable[[], _T]) -> _T:
        value = self._invoke_sync(hook, callback)
        if inspect.isawaitable(value):
            if inspect.iscoroutine(value):
                value.close()
            raise self._error(hook, "image transforms must be synchronous; replace async def with def")
        return value

    async def cleanup_after_use(self, plugin: object) -> None:
        """Run an optional hook after runtime has finished using one plugin instance."""
        hook = "cleanup_after_use"
        callback = getattr(plugin, hook, None)
        if callback is None:
            return
        if not callable(callback):
            raise self._error(hook, "cleanup hook must be callable")
        try:
            result = callback()
            if inspect.isawaitable(result):
                result = await result
        except ImageDownloaderError as exc:
            raise self._error(hook, "plugin hook failed") from exc
        except Exception as exc:
            raise self._error(hook, "plugin hook failed") from exc
        if result is not None:
            raise self._error(hook, "cleanup hook must return None")

    def apply_auth(self, flow: AuthFlow, request: RequestSpec) -> RequestSpec:
        hook = "auth_apply"
        value = self._invoke_sync(hook, lambda: flow.apply(request))
        validation.validate_request(self.plugin_id, hook, value, allow_none=False)
        assert isinstance(value, RequestSpec)
        return value

    async def initialize_auth(self, flow: AuthFlow, context: AuthContext) -> AuthLease | None:
        value = await self._invoke_async("auth_initialize", lambda: flow.initialize(context))
        if value is None:
            return None
        if not isinstance(value, AuthLease):
            raise self._error("auth_initialize", "initialize must return AuthLease")
        validation.validate_auth_data(self.plugin_id, "auth_initialize", value)
        return value

    async def login_auth(self, flow: AuthFlow, context: AuthContext) -> AuthLease:
        value = await self._invoke_async("auth_login", lambda: flow.login(context))
        if not isinstance(value, AuthLease):
            raise self._error("auth_login", "login must return AuthLease")
        validation.validate_auth_data(self.plugin_id, "auth_login", value)
        return value

    async def refresh_auth(self, flow: AuthFlow, context: AuthContext) -> AuthLease:
        value = await self._invoke_async("auth_refresh", lambda: flow.refresh(context))
        if not isinstance(value, AuthLease):
            raise self._error("auth_refresh", "refresh must return AuthLease; refresh(failed, response) was removed")
        validation.validate_auth_data(self.plugin_id, "auth_refresh", value)
        return value

    async def verify_auth(self, flow: AuthFlow, context: AuthContext) -> AuthVerification:
        value = await self._invoke_async("auth_verify", lambda: flow.verify(context))
        if not isinstance(value, AuthVerification):
            raise self._error("auth_verify", "verify must return AuthVerification")
        if value.lease is not None:
            if not isinstance(value.lease, AuthLease):
                raise self._error("auth_verify", "verification lease must be AuthLease")
            validation.validate_auth_data(self.plugin_id, "auth_verify", value.lease)
        return value

    def classify_auth(self, flow: AuthFlow, request: RequestSpec, response: RequestResponse) -> AuthDecision:
        value = self._invoke_sync("auth_classify_response", lambda: flow.classify_response(request, response))
        if not isinstance(value, AuthDecision):
            raise self._error("auth_classify_response", "classify_response must return AuthDecision")
        return value
