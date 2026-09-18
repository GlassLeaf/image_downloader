from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from PIL import Image

from image_downloader import (
    AppConfig,
    Chapter,
    DownloadManifest,
    FailureKind,
    ImageOutcomeKind,
    ImageResource,
    PluginDescriptor,
    RequestSpec,
)
from image_downloader.config import resolve_paths
from image_downloader.exceptions import DownloaderError, PluginError
from image_downloader.runtime import DownloadService, RequestGateway
from image_downloader.security import ImageProcessorRegistry, RegisteredPlugin, V2PluginRegistry


def image_bytes(fmt: str = "PNG") -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(stream, format=fmt)
    return stream.getvalue()


class Plugin:
    descriptor = PluginDescriptor("test.plugin", 10)
    last_context = None
    manifest = DownloadManifest("work", (Chapter(1, "chapter", images=(ImageResource("https://test/a"),)),))

    def matches(self, url: str) -> bool:
        return url.startswith("https://test/")

    async def inspect(self, url: str, context):
        type(self).last_context = context
        return self.manifest

    async def create_image_request(self, image, context):
        return RequestSpec(image.url)

    async def recover_image_request(self, image, failed, response, context):
        return None

    def auth_flow(self, context):
        return None

    async def transform_image(self, artifact, context):
        return artifact


def config(tmp_path: Path, **patch: object) -> AppConfig:
    value: dict[str, object] = {
        "profile": {"root": "profiles"},
        "security": {"plugin_verification": "off"},
        "logging": {"console": {"enabled": False}},
    }
    value.update(patch)
    return AppConfig.model_validate(value)


async def service_for(tmp_path: Path, plugin: type[Plugin], settings: AppConfig) -> DownloadService:
    registry = V2PluginRegistry(mode="off", catalog=None)
    registry._plugins.append(RegisteredPlugin(plugin, plugin.descriptor.id, 0))
    return DownloadService(
        settings,
        resolve_paths(settings, base_dir=tmp_path),
        registry,
        ImageProcessorRegistry(mode="off", catalog=None),
    )


async def install_transport(service: DownloadService, handler) -> None:
    old = service.gateway.client
    service.gateway.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await old.aclose()


def test_frozen_models_and_context_capability(tmp_path: Path) -> None:
    settings = config(tmp_path, plugins={"test.plugin": {"config": {"nested": "value"}}})
    assert settings.media.input_validation == "content_type"
    assert settings.media.content_type_mismatch == "accept"
    assert settings.allow_empty_manifest is False
    with pytest.raises(TypeError):
        settings.plugins["new"] = object()  # type: ignore[index]
    with pytest.raises(TypeError):
        DownloadManifest("x", (), metadata={"bad": 1})  # type: ignore[arg-type]
    request = RequestSpec("https://test/a", json={"items": ["x"]})
    with pytest.raises(TypeError):
        request.headers["x"] = "y"  # type: ignore[index]

    async def check() -> None:
        service = await service_for(tmp_path, Plugin, settings)
        await install_transport(service, lambda request: httpx.Response(200, content=image_bytes()))
        try:
            await service.run("https://test/work")
            context = Plugin.last_context
            assert context is not None
            assert set(name for name in ("config", "secrets", "requests") if hasattr(context, name)) == {
                "config",
                "secrets",
                "requests",
            }
            assert not hasattr(context.requests, "execute_image")
            with pytest.raises(TypeError):
                context.config["other"] = "x"  # type: ignore[index]
        finally:
            await service.close()

    asyncio.run(check())


def test_download_result_empty_manifest_and_ordered_partial_failures(tmp_path: Path) -> None:
    class MultiPlugin(Plugin):
        manifest = DownloadManifest(
            "work",
            (
                Chapter(
                    1, "chapter", images=(ImageResource("https://test/ok", 2), ImageResource("https://test/bad", 1))
                ),
            ),
        )

    async def check() -> None:
        service = await service_for(tmp_path, MultiPlugin, config(tmp_path))

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404 if request.url.path == "/bad" else 200, content=image_bytes())

        await install_transport(service, handler)
        try:
            result = await service.run("https://test/work")
            outcome = result.chapters[0].outcomes
            assert outcome[0].kind is ImageOutcomeKind.SAVED
            assert outcome[1].failure is not None and outcome[1].failure.kind is FailureKind.FETCH
            log = next(Path(result.saved_files[0]).parent.glob("log.log")).read_text(encoding="utf-8")
            assert log.index("download: https://test/ok") < log.index("error: image fetch error")
        finally:
            await service.close()

        class EmptyPlugin(Plugin):
            manifest = DownloadManifest("empty", ())

        empty = await service_for(tmp_path, EmptyPlugin, config(tmp_path, allow_empty_manifest=True))
        await install_transport(empty, handler)
        try:
            result = await empty.run("https://test/empty")
            assert not result.chapters
            assert list(Path(empty.paths["downloads"]).rglob("log.log"))
        finally:
            await empty.close()

    asyncio.run(check())


def test_download_failure_notifies_with_frozen_config_and_logs_every_image(tmp_path: Path) -> None:
    class ObservablePlugin(Plugin):
        manifest = DownloadManifest(
            "work",
            (
                Chapter(
                    1,
                    "chapter",
                    images=(
                        ImageResource("https://test/one", 1),
                        ImageResource("https://test/two", 2),
                        ImageResource("https://test/missing", 3),
                    ),
                ),
            ),
        )

        async def create_image_request(self, image, context):
            logging.getLogger("fixture.plugin").warning("creating image request token=fixture-secret")
            return await super().create_image_request(image, context)

    async def check() -> None:
        settings = config(
            tmp_path,
            notification={
                "enabled": True,
                "methods": ["desktop"],
                "routes": {"fetch_error": ["desktop"]},
                "notify_on": ["fetch_error"],
            },
        )
        assert isinstance(settings.notification.methods, tuple)
        assert isinstance(settings.notification.routes["fetch_error"], tuple)
        service = await service_for(tmp_path, ObservablePlugin, settings)
        messages: list[str] = []

        async def desktop(_title: str, message: str) -> bool:
            messages.append(message)
            return True

        service.notifications._desktop = desktop  # type: ignore[method-assign]
        await install_transport(
            service,
            lambda request: httpx.Response(404 if request.url.path == "/missing" else 200, content=image_bytes()),
        )
        try:
            result = await service.run("https://test/work")
            assert len(result.saved_files) == 2
            assert len(result.failures) == 1
        finally:
            await service.close()

        debug_log = (tmp_path / "profiles" / "default" / "logs" / "debug.log").read_text(encoding="utf-8")
        assert len(messages) == 1 and "fetch_error: 1" in messages[0]
        assert debug_log.count("event=request_started module=download") == 3
        assert debug_log.count("event=download_finished module=download") == 3
        assert debug_log.count("action=image_fetch") == 6
        assert debug_log.count("event=request_started module=http") == 3
        assert "event=python_log level=WARNING logger=fixture.plugin" in debug_log
        assert "fixture-secret" not in debug_log

    asyncio.run(check())


def test_terminal_download_error_is_notified_before_run_reraises(tmp_path: Path) -> None:
    async def check() -> None:
        settings = config(
            tmp_path,
            continue_on_error=False,
            network={"max_retries": 1},
            notification={"enabled": True, "methods": ["desktop"], "notify_on": ["fetch_error"]},
        )
        service = await service_for(tmp_path, Plugin, settings)
        messages: list[str] = []

        async def desktop(_title: str, message: str) -> bool:
            messages.append(message)
            return True

        service.notifications._desktop = desktop  # type: ignore[method-assign]
        await install_transport(service, lambda request: httpx.Response(404))
        try:
            with pytest.raises(DownloaderError):
                await service.run("https://test/work")
        finally:
            await service.close()
        assert len(messages) == 1 and "fetch_error: 1" in messages[0]

    asyncio.run(check())


def test_existing_file_modes_and_fail_fast_queue(tmp_path: Path) -> None:
    async def save_once(mode: str):
        service = await service_for(tmp_path, Plugin, config(tmp_path, output={"existing_file": mode}))
        await install_transport(service, lambda request: httpx.Response(200, content=image_bytes()))
        try:
            return await service.run("https://test/work")
        finally:
            await service.close()

    async def check() -> None:
        first = await save_once("overwrite")
        second = await save_once("rename")
        third = await save_once("skip")
        assert first.saved_files[0] != second.saved_files[0]
        assert third.skipped_files

        calls: list[str] = []

        class FailingPlugin(Plugin):
            manifest = DownloadManifest(
                "work", (Chapter(1, "chapter", images=tuple(ImageResource(f"https://test/{i}", i) for i in range(6))),)
            )

            async def create_image_request(self, image, context):
                calls.append(image.url)
                return RequestSpec(image.url)

        fail = await service_for(
            tmp_path,
            FailingPlugin,
            config(tmp_path, continue_on_error=False, network={"max_concurrency": 2}),
        )
        await install_transport(fail, lambda request: httpx.Response(500))
        try:
            with pytest.raises(DownloaderError):
                await fail.run("https://test/work")
            assert len(calls) <= 2
        finally:
            await fail.close()

    asyncio.run(check())


def test_auth_coalescing_and_single_recovery() -> None:
    class Flow:
        version = 0
        refreshed = 0

        def is_auth_failure(self, request, response) -> bool:
            return response.body == b"login"

        async def apply(self, request):
            return replace(request, headers={"X-Auth": str(self.version)})

        async def refresh(self, failed, response):
            self.refreshed += 1
            self.version = 1
            return failed

    async def check() -> None:
        gateway = RequestGateway(AppConfig.model_validate({"security": {"plugin_verification": "off"}}))
        old = gateway.client
        seen: list[str] = []

        def auth_handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers.get("X-Auth", ""))
            return httpx.Response(200, content=b"ok" if request.headers.get("X-Auth") == "1" else b"login")

        gateway.client = httpx.AsyncClient(transport=httpx.MockTransport(auth_handler))
        await old.aclose()
        flow = Flow()
        gateway.bind_auth_flow(flow)
        try:
            await asyncio.gather(
                gateway.execute(RequestSpec("https://test/a")), gateway.execute(RequestSpec("https://test/b"))
            )
            assert flow.refreshed == 1
            assert "1" in seen
        finally:
            await gateway.close()

        class RecoverPlugin(Plugin):
            async def recover_image_request(self, image, failed, response, context):
                return RequestSpec("https://test/fixed")

        service = await service_for(Path.cwd(), RecoverPlugin, config(Path.cwd()))
        attempts: list[str] = []
        await install_transport(
            service,
            lambda request: (
                attempts.append(request.url.path)
                or httpx.Response(200 if request.url.path == "/fixed" else 404, content=image_bytes())
            ),
        )
        try:
            assert (await service.run("https://test/work")).saved_files
            assert attempts == ["/a", "/fixed"]
        finally:
            await service.close()

    asyncio.run(check())


def test_plugin_contract_failure_is_not_an_image_outcome(tmp_path: Path) -> None:
    class BadPlugin(Plugin):
        async def create_image_request(self, image, context):
            return object()

    async def check() -> None:
        service = await service_for(tmp_path, BadPlugin, config(tmp_path))
        try:
            with pytest.raises(PluginError):
                await service.run("https://test/work")
        finally:
            await service.close()

    asyncio.run(check())
