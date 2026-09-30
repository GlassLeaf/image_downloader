from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from image_downloader.config import AppConfig
from image_downloader.exceptions import HttpTransportError, PluginError
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
            DownloadManifest("Book", (Chapter(1, "Chapter", images=(ImageResource(""),)),)),
            r"invalid image locator at chapters\[0\]\.images\[0\]",
        ),
        (
            DownloadManifest("Book", (Chapter(1, "Chapter", images=(ImageResource(" \t"),)),)),
            r"invalid image locator at chapters\[0\]\.images\[0\]",
        ),
        (
            DownloadManifest(
                "Book", (Chapter(1, "Chapter", images=(ImageResource("image:1", original_filename=""),)),)
            ),
            r"invalid original filename at chapters\[0\]\.images\[0\]",
        ),
        (
            DownloadManifest(
                "Book",
                (
                    Chapter(1, "Chapter", images=(ImageResource("image:1", original_filename="folder/name.webp"),)),
                ),
            ),
            r"invalid original filename at chapters\[0\]\.images\[0\]",
        ),
        (
            DownloadManifest(
                "Book",
                (Chapter(1, "Chapter", images=(ImageResource("image:1", original_filename=cast(str, 1)),)),),
            ),
            r"invalid original filename at chapters\[0\]\.images\[0\]",
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


def test_invoker_accepts_an_opaque_image_locator_at_the_manifest_boundary() -> None:
    image = ImageResource("image:42", image_id="42")

    class ManifestPlugin:
        async def inspect(self, _url: str, _context: object) -> DownloadManifest:
            return DownloadManifest("Book", (Chapter(1, "One", images=(image,)),))

    async def scenario() -> None:
        manifest = await PluginInvoker("com.example.opaque-locator").inspect(
            cast(SitePlugin, ManifestPlugin()),
            "https://example.test/gallery",
            _context(),
        )
        assert manifest.chapters[0].images == (image,)

    asyncio.run(scenario())


def test_image_resource_original_filename_defaults_to_none() -> None:
    assert ImageResource("image:42").original_filename is None


def test_invoker_accepts_an_extensionless_original_filename_at_the_manifest_boundary() -> None:
    image = ImageResource("image:42", original_filename="cover")

    class ManifestPlugin:
        async def inspect(self, _url: str, _context: object) -> DownloadManifest:
            return DownloadManifest("Book", (Chapter(1, "One", images=(image,)),))

    async def scenario() -> None:
        manifest = await PluginInvoker("com.example.original-filename").inspect(
            cast(SitePlugin, ManifestPlugin()),
            "https://example.test/gallery",
            _context(),
        )
        assert manifest.chapters[0].images[0].original_filename == "cover"

    asyncio.run(scenario())


def test_gateway_resolves_an_opaque_locator_before_any_transport_request() -> None:
    class ResolvingPlugin:
        def __init__(self) -> None:
            self.locators: list[str] = []

        async def create_image_request(self, image: ImageResource, _context: object) -> RequestSpec:
            self.locators.append(image.url)
            assert image.image_id == "42"
            return RequestSpec("https://cdn.example.test/signed/42?signature=current")

    async def scenario() -> None:
        gateway = RequestGateway(AppConfig())
        plugin = ResolvingPlugin()
        sent: list[RequestSpec] = []

        async def transport(spec: RequestSpec, **_kwargs: object) -> RequestResponse:
            sent.append(spec)
            return RequestResponse(spec.url, 200, {}, b"image")

        gateway._transport = transport  # type: ignore[method-assign]
        try:
            response = await gateway.operation(
                plugin_id="com.example.opaque-locator",
                operation_url="https://example.test/gallery",
            ).execute_image(
                cast(SitePlugin, plugin),
                ImageResource("image:42", image_id="42"),
                _context(),
            )
            assert response.status == 200
            assert plugin.locators == ["image:42"]
            assert [spec.url for spec in sent] == ["https://cdn.example.test/signed/42?signature=current"]
        finally:
            await gateway.close()

    asyncio.run(scenario())


def test_image_metadata_is_immutable_and_reaches_transform_context() -> None:
    image = ImageResource("image:42", image_id="42", metadata={"page": "7", "variant": "full"})
    manifest = DownloadManifest("Book", (Chapter(1, "One", images=(image,)),))
    context = TransformContext(image, {}, {}, {}, None, manifest, manifest.chapters[0])

    assert context.image_metadata == {"page": "7", "variant": "full"}
    with pytest.raises(TypeError):
        image.metadata["page"] = "8"  # type: ignore[index]
    with pytest.raises(TypeError):
        context.image_metadata["page"] = "8"  # type: ignore[index]


def test_operation_request_cap_serializes_transport_without_holding_hook_execution() -> None:
    class ResolvingPlugin:
        async def create_image_request(self, image: ImageResource, context: PluginExecutionContext) -> RequestSpec:
            response = await context.requests.execute(RequestSpec("https://example.test/key"))
            assert response.status == 200
            return RequestSpec(f"https://example.test/image/{image.image_id}")

    async def scenario() -> None:
        gateway = RequestGateway(AppConfig())
        active = maximum = 0
        entered = asyncio.Event()
        release = asyncio.Event()

        async def transport(spec: RequestSpec, **_kwargs: object) -> RequestResponse:
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            entered.set()
            await release.wait()
            active -= 1
            return RequestResponse(spec.url, 200, {}, b"ok")

        gateway._transport = transport  # type: ignore[method-assign]
        session = gateway.operation(
            plugin_id="com.example.policy",
            operation_url="https://example.test/gallery",
            request_concurrency=1,
        )
        context = PluginExecutionContext({}, {}, {}, None, cast(object, object()), session)
        try:
            first = asyncio.create_task(session.execute(RequestSpec("https://example.test/one")))
            await entered.wait()
            second = asyncio.create_task(session.execute(RequestSpec("https://example.test/two")))
            await asyncio.sleep(0)
            assert maximum == 1
            release.set()
            await asyncio.gather(first, second)
            assert maximum == 1

            response = await session.execute_image(
                cast(SitePlugin, ResolvingPlugin()),
                ImageResource("image:42", image_id="42"),
                context,
            )
            assert response.url == "https://example.test/image/42"
        finally:
            release.set()
            await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("request_spec", "message"),
    (
        (RequestSpec("image:42"), r"create_image_request returned an invalid request URL"),
        (
            RequestSpec("https://cdn.example.test/image.jpg", referer="image:42"),
            r"create_image_request returned an invalid referer",
        ),
    ),
)
def test_request_urls_and_referers_remain_absolute_http_urls(request_spec: RequestSpec, message: str) -> None:
    class InvalidRequestPlugin:
        async def create_image_request(self, _image: ImageResource, _context: object) -> RequestSpec:
            return request_spec

    async def scenario() -> None:
        with pytest.raises(PluginError, match=message):
            await PluginInvoker("com.example.invalid-request").create_image_request(
                cast(SitePlugin, InvalidRequestPlugin()),
                ImageResource("image:42"),
                _context(),
            )

    asyncio.run(scenario())


def test_site_and_processor_transforms_can_return_an_opaque_source_locator() -> None:
    class OpaqueSiteTransform:
        async def transform_image(self, artifact: ImageArtifact, _context: object) -> ImageArtifact:
            return artifact

    class OpaqueProcessorTransform:
        async def transform(self, artifact: ImageArtifact, _context: object) -> ImageArtifact:
            return artifact

    async def scenario() -> None:
        artifact = ImageArtifact(b"image", "image/jpeg", "image:42", image_id="42")
        context = cast(TransformContext, object())
        site_result = await PluginInvoker("com.example.opaque-site").transform_image(
            cast(SitePlugin, OpaqueSiteTransform()),
            artifact,
            context,
            chapter_id="1",
        )
        processor_result = await PluginInvoker("com.example.opaque-processor").transform_processor(
            cast(ImageProcessor, OpaqueProcessorTransform()),
            artifact,
            context,
            chapter_id="1",
        )
        assert site_result.source_url == "image:42"
        assert processor_result.source_url == "image:42"

    asyncio.run(scenario())


def test_recovery_is_called_once_for_http_status_but_never_for_transport_failure() -> None:
    class RecoveringPlugin:
        def __init__(self) -> None:
            self.created = 0
            self.recovered = 0

        async def create_image_request(self, _image: ImageResource, _context: object) -> RequestSpec:
            self.created += 1
            return RequestSpec("https://example.test/original.jpg")

        async def recover_image_request(
            self,
            _image: ImageResource,
            _failed: RequestSpec,
            _response: RequestResponse,
            _context: object,
        ) -> RequestSpec:
            self.recovered += 1
            return RequestSpec("https://example.test/reissued.jpg")

    async def scenario() -> None:
        gateway = RequestGateway(AppConfig())
        plugin = RecoveringPlugin()
        responses = [
            RequestResponse("https://example.test/original.jpg", 500, {}, b""),
            RequestResponse("https://example.test/reissued.jpg", 200, {}, b"ok"),
        ]

        async def status_transport(_spec: RequestSpec, **_kwargs: object) -> RequestResponse:
            return responses.pop(0)

        gateway._transport = status_transport  # type: ignore[method-assign]
        try:
            response = await gateway.operation(
                plugin_id="com.example.recovery", operation_url="https://example.test/gallery"
            ).execute_image(cast(SitePlugin, plugin), ImageResource("https://example.test/canonical.jpg"), _context())
            assert response.status == 200
            assert plugin.created == 1
            assert plugin.recovered == 1

            async def failed_transport(_spec: RequestSpec, **_kwargs: object) -> RequestResponse:
                raise HttpTransportError("offline")

            gateway._transport = failed_transport  # type: ignore[method-assign]
            with pytest.raises(HttpTransportError):
                await gateway.operation(
                    plugin_id="com.example.recovery", operation_url="https://example.test/gallery"
                ).execute_image(
                    cast(SitePlugin, plugin), ImageResource("https://example.test/canonical-2.jpg"), _context()
                )
            assert plugin.created == 2
            assert plugin.recovered == 1
        finally:
            await gateway.close()

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
