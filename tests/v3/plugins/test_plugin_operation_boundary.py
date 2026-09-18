from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from image_downloader.config import AppConfig
from image_downloader.exceptions import PluginError
from image_downloader.models import (
    Chapter,
    DownloadManifest,
    ImageArtifact,
    ImageResource,
    RequestResponse,
    RequestSpec,
    UpdateCandidate,
    UpdateSnapshot,
)
from image_downloader.observability.logging import DownloadLogger
from image_downloader.observability.scope import OperationDiagnosticsScope
from image_downloader.plugins.plugin_invoker import PluginInvoker
from image_downloader.ports import (
    ImageProcessor,
    PluginExecutionContext,
    SitePlugin,
    TransformContext,
    UpdateProvider,
)
from image_downloader.runtime import RuntimeComposer
from image_downloader.transport.gateway import RequestGateway


def _context() -> PluginExecutionContext:
    return cast(PluginExecutionContext, object())


def test_invoker_converts_unexpected_hook_exceptions_with_plugin_and_hook_identity() -> None:
    class BrokenPlugin:
        async def inspect(self, _url: str, _context: object) -> DownloadManifest:
            raise RuntimeError("callback failed")

    async def scenario() -> None:
        with pytest.raises(PluginError, match=r"plugin=com\.example\.broken, hook=inspect") as caught:
            await PluginInvoker("com.example.broken").inspect(
                cast(SitePlugin, BrokenPlugin()),
                "https://example.test/gallery",
                _context(),
            )
        assert isinstance(caught.value.__cause__, RuntimeError)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("manifest", "message"),
    (
        (
            DownloadManifest("Book", (cast(Chapter, "not-a-chapter"),)),
            r"invalid chapter at chapters\[0\]",
        ),
        (
            DownloadManifest("Book", (Chapter(1, "Chapter", images=(cast(ImageResource, "not-an-image"),)),)),
            r"invalid image at chapters\[0\]\.images\[0\]",
        ),
        (
            DownloadManifest("Book", (Chapter(1, "Chapter", images=(ImageResource("relative.jpg"),)),)),
            r"invalid image URL at chapters\[0\]\.images\[0\]",
        ),
    ),
)
def test_invoker_rejects_invalid_nested_manifest_values(manifest: DownloadManifest, message: str) -> None:
    class ManifestPlugin:
        async def inspect(self, _url: str, _context: object) -> DownloadManifest:
            return manifest

    async def scenario() -> None:
        with pytest.raises(PluginError, match=message):
            await PluginInvoker("com.example.invalid").inspect(
                cast(SitePlugin, ManifestPlugin()),
                "https://example.test/gallery",
                _context(),
            )

    asyncio.run(scenario())


def test_invoker_rejects_invalid_nested_update_candidate() -> None:
    snapshot = UpdateSnapshot(
        "https://example.test/feed",
        (cast(UpdateCandidate, "not-a-candidate"),),
        datetime.now(UTC),
    )

    class UpdatePlugin:
        async def check_updates(self, _url: str, _context: object) -> UpdateSnapshot:
            return snapshot

    async def scenario() -> None:
        with pytest.raises(PluginError, match=r"invalid candidate at candidates\[0\]"):
            await PluginInvoker("com.example.invalid").check_updates(
                cast(UpdateProvider, UpdatePlugin()),
                "https://example.test/feed",
                _context(),
            )

    asyncio.run(scenario())


def test_request_and_auth_hooks_use_the_shared_invocation_boundary() -> None:
    class BrokenRequestPlugin:
        async def create_image_request(self, _image: object, _context: object) -> RequestSpec:
            raise LookupError("request callback failed")

    class BrokenFlow:
        async def apply(self, _request: RequestSpec) -> RequestSpec:
            raise RuntimeError("auth callback failed")

        async def refresh(self, request: RequestSpec, _response: RequestResponse) -> RequestSpec:
            return request

        def is_auth_failure(self, _request: RequestSpec, _response: RequestResponse) -> bool:
            return False

    async def scenario() -> None:
        gateway = RequestGateway(AppConfig())
        try:
            request_session = gateway.operation(
                plugin_id="com.example.request",
                operation_url="https://example.test/gallery",
            )
            with pytest.raises(PluginError, match="hook=create_image_request"):
                await request_session.execute_image(
                    cast(SitePlugin, BrokenRequestPlugin()),
                    ImageResource("https://example.test/image.jpg"),
                    _context(),
                )

            auth_session = gateway.operation(
                plugin_id="com.example.auth",
                operation_url="https://example.test/gallery",
                auth_flow_factory=lambda _: BrokenFlow(),
            )
            with pytest.raises(PluginError, match="hook=auth_apply"):
                await auth_session.execute(RequestSpec("https://example.test/image.jpg"))
        finally:
            await gateway.close()

    asyncio.run(scenario())


def test_sync_matching_and_configuration_hooks_use_the_shared_boundary() -> None:
    class BrokenMatchPlugin:
        def matches(self, _url: str) -> bool:
            raise RuntimeError("match failed")

    class BrokenConfigPlugin:
        def validate_config(self, _config: object, _app_settings: object) -> None:
            raise RuntimeError("configuration failed")

    invoker = PluginInvoker("com.example.sync")
    with pytest.raises(PluginError, match="hook=matches"):
        invoker.matches(
            cast(SitePlugin, BrokenMatchPlugin()),
            "https://example.test/gallery",
            config={},
            app_settings={},
        )
    with pytest.raises(PluginError, match="hook=validate_config"):
        invoker.validate_config(cast(SitePlugin, BrokenConfigPlugin()), {}, {})


def test_transform_and_recovery_hooks_use_the_shared_boundary() -> None:
    class InvalidTransformPlugin:
        async def transform_image(self, _artifact: object, _context: object) -> object:
            return object()

        async def recover_image_request(
            self,
            _image: object,
            _failed: object,
            _response: object,
            _context: object,
        ) -> RequestSpec | None:
            raise RuntimeError("recovery failed")

    class BrokenProcessor:
        async def transform(self, _artifact: object, _context: object) -> ImageArtifact:
            raise RuntimeError("processor failed")

    async def scenario() -> None:
        invoker = PluginInvoker("com.example.transform")
        artifact = ImageArtifact(b"image", "image/jpeg", "https://example.test/image.jpg")
        transform_context = cast(TransformContext, object())
        plugin = cast(SitePlugin, InvalidTransformPlugin())
        with pytest.raises(PluginError, match="hook=transform_image"):
            await invoker.transform_image(plugin, artifact, transform_context, chapter_id="1")
        with pytest.raises(PluginError, match="hook=transform"):
            await PluginInvoker("com.example.processor").transform_processor(
                cast(ImageProcessor, BrokenProcessor()),
                artifact,
                transform_context,
                chapter_id="1",
            )
        with pytest.raises(PluginError, match="hook=recover_image_request"):
            await invoker.recover_image_request(
                plugin,
                ImageResource(artifact.source_url),
                RequestSpec(artifact.source_url),
                RequestResponse(artifact.source_url, 500, {}, b""),
                _context(),
            )

    asyncio.run(scenario())


class _FailingFlushLogger(DownloadLogger):
    def __init__(self) -> None:
        super().__init__()
        self.ended = False

    async def flush_python_log_capture(self) -> None:
        raise OSError("debug sink failed")

    def end_python_log_capture(self) -> None:
        self.ended = True
        super().end_python_log_capture()


def test_diagnostics_scope_preserves_primary_error_and_always_detaches_handler() -> None:
    async def scenario() -> None:
        logger = _FailingFlushLogger()
        namespace = "_image_downloader_plugins.scope_test"
        plugin_logger = logging.getLogger(namespace)
        original_handlers = tuple(plugin_logger.handlers)
        finalized = False

        async def fail_finalizer() -> None:
            nonlocal finalized
            finalized = True
            raise OSError("notification flush failed")

        with pytest.raises(RuntimeError, match="primary failure"):
            async with OperationDiagnosticsScope(logger, fail_finalizer) as diagnostics:
                diagnostics.capture((namespace,))
                raise RuntimeError("primary failure")

        assert finalized
        assert logger.ended
        assert tuple(plugin_logger.handlers) == original_handlers

    asyncio.run(scenario())


def test_diagnostics_scope_warns_without_replacing_success_after_cleanup_failure(capsys) -> None:
    async def scenario() -> None:
        logger = _FailingFlushLogger()

        async def finalize() -> None:
            return None

        async with OperationDiagnosticsScope(logger, finalize) as diagnostics:
            diagnostics.capture(("_image_downloader_plugins.scope_success",))
        assert logger.ended

    asyncio.run(scenario())
    assert "warning: diagnostic logging failed" in capsys.readouterr().err


def test_download_service_routes_inspect_through_plugin_invoker(tmp_path: Path) -> None:
    class BrokenInspectPlugin:
        def auth_flow(self, _context: object) -> None:
            return None

        async def inspect(self, _url: str, _context: object) -> DownloadManifest:
            raise RuntimeError("inspect callback failed")

    async def scenario() -> None:
        data_root = (tmp_path / "data").resolve()
        plugin_root = (tmp_path / "plugins").resolve()
        config = AppConfig.model_validate(
            {
                "storage": {"data_root": str(data_root)},
                "plugins": {"root": str(plugin_root)},
                "security": {"plugin_verification": "off"},
                "logging": {"console": {"enabled": False}},
                "notification": {"enabled": False},
            }
        )
        service = RuntimeComposer(config, config_root=tmp_path.resolve(), plugin_root=plugin_root).compose()
        record = service.registry.records["core.generic-html"]
        plugin = BrokenInspectPlugin()
        service.registry.resolve = lambda *_args, **_kwargs: (record, plugin)  # type: ignore[method-assign]
        try:
            with pytest.raises(PluginError, match="hook=inspect") as caught:
                await service.run("https://example.test/gallery")
            assert isinstance(caught.value.__cause__, RuntimeError)
        finally:
            await service.close()

    asyncio.run(scenario())
