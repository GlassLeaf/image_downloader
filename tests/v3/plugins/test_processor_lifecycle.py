from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from PIL import Image
from test_plugin_v3 import make_plugin

from image_downloader.config import AppConfig
from image_downloader.exceptions import PluginError
from image_downloader.models import ImageArtifact
from image_downloader.runtime import DownloadService, RuntimeComposer


def _service(tmp_path: Path, *plugin_ids: str) -> DownloadService:
    root = (tmp_path / "plugins").resolve()
    for plugin_id in plugin_ids:
        make_plugin(root, plugin_id=plugin_id, kind="image_processor_plugin")
    config = AppConfig.model_validate(
        {
            "storage": {"data_root": str((tmp_path / "data").resolve())},
            "plugins": {"root": str(root)},
            "security": {"plugin_verification": "off"},
            "image_processors": {"chain": list(plugin_ids)},
            "output": {"existing_file": "overwrite"},
            "logging": {"console": {"enabled": False}},
            "notification": {"enabled": False},
        }
    )
    return RuntimeComposer(config, config_root=tmp_path.resolve(), plugin_root=root).compose()


async def _mock_gallery(service: DownloadService, *, images: int = 2) -> list[str]:
    await service.gateway.client.aclose()
    output = BytesIO()
    Image.new("RGB", (2, 2), "red").save(output, format="PNG")
    png = output.getvalue()
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/gallery":
            tags = "".join(f"<img src='/image-{index}.png'>" for index in range(images))
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                content=f"<title>Gallery</title>{tags}".encode(),
                request=request,
            )
        return httpx.Response(200, headers={"content-type": "image/png"}, content=png, request=request)

    service.gateway.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return requests


def test_processor_is_constructed_and_validated_once_per_operation_and_serialized(tmp_path: Path) -> None:
    async def scenario() -> None:
        plugin_id = "com.example.stateful"
        service = _service(tmp_path, plugin_id)
        requests = await _mock_gallery(service)
        instances: list[StatefulProcessor] = []

        class StatefulProcessor:
            def __init__(self) -> None:
                self.validations = 0
                self.transforms = 0
                self.closes = 0
                self.active = 0
                self.max_active = 0
                instances.append(self)

            def validate_config(self, _config: object, _app_settings: object) -> None:
                self.validations += 1

            async def transform(self, artifact: ImageArtifact, _context: object) -> ImageArtifact:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                await asyncio.sleep(0.01)
                self.active -= 1
                self.transforms += 1
                return artifact

            def close(self) -> None:
                self.closes += 1

        service.registry.loader.register_class(service.registry.records[plugin_id], StatefulProcessor)
        try:
            first = await service.run("https://example.test/gallery")
            assert len(first.saved_files) == 2
            assert len(instances) == 1
            assert (instances[0].validations, instances[0].transforms, instances[0].closes) == (1, 2, 1)
            assert instances[0].max_active == 1

            second = await service.run("https://example.test/gallery")
            assert len(second.saved_files) == 2
            assert len(instances) == 2
            assert (instances[1].validations, instances[1].transforms, instances[1].closes) == (1, 2, 1)
            assert requests.count("/gallery") == 2
        finally:
            await service.close()

    asyncio.run(scenario())


def test_processor_validation_fails_before_any_request_and_closes_instance(tmp_path: Path) -> None:
    async def scenario() -> None:
        plugin_id = "com.example.invalid"
        service = _service(tmp_path, plugin_id)
        requests = await _mock_gallery(service)
        closed: list[bool] = []

        class InvalidProcessor:
            def validate_config(self, _config: object, _app_settings: object) -> None:
                raise ValueError("bad configuration")

            async def transform(self, artifact: ImageArtifact, _context: object) -> ImageArtifact:
                return artifact

            def close(self) -> None:
                closed.append(True)

        service.registry.loader.register_class(service.registry.records[plugin_id], InvalidProcessor)
        try:
            with pytest.raises(PluginError, match="hook=validate_config"):
                await service.run("https://example.test/gallery")
            assert requests == []
            assert closed == [True]
        finally:
            await service.close()

    asyncio.run(scenario())


def test_partial_chain_initialization_closes_every_constructed_instance_in_reverse_order(tmp_path: Path) -> None:
    async def scenario() -> None:
        first_id, second_id = "com.example.first", "com.example.second"
        service = _service(tmp_path, first_id, second_id)
        requests = await _mock_gallery(service)
        closed: list[str] = []

        class FirstProcessor:
            def validate_config(self, _config: object, _app_settings: object) -> None:
                return None

            async def transform(self, artifact: ImageArtifact, _context: object) -> ImageArtifact:
                return artifact

            def close(self) -> None:
                closed.append(first_id)

        class SecondProcessor(FirstProcessor):
            def validate_config(self, _config: object, _app_settings: object) -> None:
                raise ValueError("invalid second processor")

            def close(self) -> None:
                closed.append(second_id)

        service.registry.loader.register_class(service.registry.records[first_id], FirstProcessor)
        service.registry.loader.register_class(service.registry.records[second_id], SecondProcessor)
        try:
            with pytest.raises(PluginError, match="hook=validate_config"):
                await service.run("https://example.test/gallery")
            assert requests == []
            assert closed == [second_id, first_id]
        finally:
            await service.close()

    asyncio.run(scenario())


def test_async_cleanup_failure_becomes_plugin_error_after_successful_processing(tmp_path: Path) -> None:
    async def scenario() -> None:
        plugin_id = "com.example.async-close"
        service = _service(tmp_path, plugin_id)
        await _mock_gallery(service, images=1)
        calls: list[str] = []

        class AsyncCloseProcessor:
            def validate_config(self, _config: object, _app_settings: object) -> None:
                return None

            async def transform(self, artifact: ImageArtifact, _context: object) -> ImageArtifact:
                return artifact

            async def aclose(self) -> None:
                calls.append("aclose")
                raise RuntimeError("close failed")

            def close(self) -> None:
                calls.append("close")

        service.registry.loader.register_class(service.registry.records[plugin_id], AsyncCloseProcessor)
        try:
            with pytest.raises(PluginError, match="hook=aclose"):
                await service.run("https://example.test/gallery")
            assert calls == ["aclose"]
        finally:
            await service.close()

    asyncio.run(scenario())


def test_cleanup_failure_does_not_replace_transform_failure(tmp_path: Path) -> None:
    async def scenario() -> None:
        plugin_id = "com.example.failing-transform"
        service = _service(tmp_path, plugin_id)
        await _mock_gallery(service, images=1)
        closed: list[bool] = []

        class FailingProcessor:
            def validate_config(self, _config: object, _app_settings: object) -> None:
                return None

            async def transform(self, _artifact: ImageArtifact, _context: object) -> ImageArtifact:
                raise RuntimeError("transform failed")

            def close(self) -> None:
                closed.append(True)
                raise RuntimeError("close failed")

        service.registry.loader.register_class(service.registry.records[plugin_id], FailingProcessor)
        try:
            with pytest.raises(PluginError, match="hook=transform"):
                await service.run("https://example.test/gallery")
            assert closed == [True]
        finally:
            await service.close()

    asyncio.run(scenario())


def test_cancelled_operation_closes_prepared_processor(tmp_path: Path) -> None:
    async def scenario() -> None:
        plugin_id = "com.example.cancelled"
        service = _service(tmp_path, plugin_id)
        await _mock_gallery(service, images=1)
        transforming = asyncio.Event()
        closed: list[bool] = []

        class WaitingProcessor:
            def validate_config(self, _config: object, _app_settings: object) -> None:
                return None

            async def transform(self, _artifact: ImageArtifact, _context: object) -> ImageArtifact:
                transforming.set()
                await asyncio.Future()
                raise AssertionError("unreachable")

            def close(self) -> None:
                closed.append(True)

        service.registry.loader.register_class(service.registry.records[plugin_id], WaitingProcessor)
        try:
            task = asyncio.create_task(service.run("https://example.test/gallery"))
            await asyncio.wait_for(transforming.wait(), timeout=5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert closed == [True]
        finally:
            await service.close()

    asyncio.run(scenario())
