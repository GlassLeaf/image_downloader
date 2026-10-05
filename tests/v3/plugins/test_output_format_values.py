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
from image_downloader.models import Chapter, DownloadManifest, ImageArtifact, ImageResource, RequestSpec
from image_downloader.runtime import RuntimeComposer

SITE_ID = "com.example.gallery"
PROCESSOR_ID = "com.example.grayscale"


def _png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (2, 2), "blue").save(output, format="PNG")
    return output.getvalue()


def _service(
    tmp_path: Path,
    *,
    directory_format: str = "%CHAPTER_NUMBER%_%CONTENT_TITLE%_%CHAPTER_TITLE%",
    filename_format: str = "%IMAGE_INDEX%.%EXT%",
    processors: tuple[str, ...] = (),
    output_root: Path | None = None,
):
    plugin_root = (tmp_path / "plugins").resolve()
    make_plugin(plugin_root, plugin_id=SITE_ID)
    for processor_id in processors:
        make_plugin(plugin_root, plugin_id=processor_id, kind="image_processor_plugin")
    config = AppConfig.model_validate(
        {
            "storage": {"data_root": str((tmp_path / "data").resolve())},
            "plugins": {"root": str(plugin_root)},
            "security": {"plugin_verification": "off"},
            "output": {"directory_format": directory_format, "filename_format": filename_format},
            "image_processors": {"chain": list(processors)},
            "logging": {"console": {"enabled": False}},
            "notification": {"enabled": False},
        }
    )
    return RuntimeComposer(
        config,
        config_root=tmp_path.resolve(),
        plugin_root=plugin_root,
        output_root=output_root,
    ).compose()


def test_site_and_processor_values_are_collected_once_before_inspection_and_reused(tmp_path: Path) -> None:
    async def scenario() -> None:
        output_root = (tmp_path / "custom-output").resolve()
        service = _service(
            tmp_path,
            directory_format=f"%CHAPTER_NUMBER%_%PLUGIN[{SITE_ID}:SITE_TAG]%_%PLUGIN[{PROCESSOR_ID}:FILTER_NAME]%",
            filename_format=f"%IMAGE_INDEX%%PLUGIN[{SITE_ID}:SITE_TAG]%_%PLUGIN[{PROCESSOR_ID}:FILTER_NAME]%.%EXT%",
            processors=(PROCESSOR_ID,),
            output_root=output_root,
        )
        order: list[str] = []
        seen: dict[str, object] = {}

        class Site:
            calls = 0

            def validate_config(self, _config: object, _app_settings: object) -> None:
                return None

            def matches(self, _url: str) -> bool:
                return True

            def output_format_values(self, context: object) -> dict[str, str]:
                Site.calls += 1
                order.append("site-values")
                seen["site"] = context
                return {"SITE_TAG": "_site"}

            async def inspect(self, _url: str, _context: object) -> DownloadManifest:
                order.append("inspect")
                return DownloadManifest("Book", (Chapter(1, "One", images=(ImageResource("image:1"),)),))

            async def create_image_request(self, _image: ImageResource, _context: object) -> RequestSpec:
                return RequestSpec("https://example.test/image")

            async def recover_image_request(self, *_args: object) -> None:
                return None

            def auth_flow(self, _context: object) -> None:
                return None

            async def transform_image(self, artifact: ImageArtifact, _context: object) -> ImageArtifact:
                return artifact

        class Processor:
            calls = 0

            def validate_config(self, _config: object, _app_settings: object) -> None:
                return None

            def output_format_values(self, context: object) -> dict[str, str]:
                Processor.calls += 1
                order.append("processor-values")
                seen["processor"] = context
                return {"FILTER_NAME": "_grayscale"}

            async def transform(self, artifact: ImageArtifact, _context: object) -> ImageArtifact:
                return artifact

        service.registry.loader.register_class(service.registry.records[SITE_ID], Site)
        service.registry.loader.register_class(service.registry.records[PROCESSOR_ID], Processor)
        await service.gateway.client.aclose()
        service.gateway.client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, headers={"content-type": "image/png"}, content=_png(), request=request
                )
            )
        )
        try:
            result = await service.run("https://example.test/gallery")
        finally:
            await service.close()

        assert len(result.saved_files) == 1
        assert Site.calls == Processor.calls == 1
        assert order[:2] == ["site-values", "processor-values"]
        assert order[2] == "inspect"
        assert seen["site"].plugin_id == SITE_ID  # type: ignore[attr-defined]
        assert seen["site"].plugin_kind == "site_plugin"  # type: ignore[attr-defined]
        assert seen["processor"].plugin_id == PROCESSOR_ID  # type: ignore[attr-defined]
        with pytest.raises(AttributeError, match="immutable"):
            seen["site"].operation_url = "https://example.test/other"  # type: ignore[attr-defined]
        saved = Path(result.chapters[0].outcomes[0].path or "")
        assert saved.is_relative_to(output_root)
        assert saved.name == "0001_site_grayscale.png"
        assert saved.parent.name == "0001_site_grayscale"

    asyncio.run(scenario())


def test_output_format_value_exception_fails_before_inspection(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = _service(tmp_path)
        inspected = False

        class Site:
            def validate_config(self, _config: object, _app_settings: object) -> None:
                return None

            def matches(self, _url: str) -> bool:
                return True

            def output_format_values(self, _context: object) -> dict[str, str]:
                raise RuntimeError("provider failure")

            async def inspect(self, _url: str, _context: object) -> DownloadManifest:
                nonlocal inspected
                inspected = True
                return DownloadManifest("Book", ())

            async def create_image_request(self, *_args: object) -> RequestSpec:
                raise AssertionError("inspection must not run")

            async def recover_image_request(self, *_args: object) -> None:
                return None

            def auth_flow(self, _context: object) -> None:
                return None

            async def transform_image(self, artifact: ImageArtifact, _context: object) -> ImageArtifact:
                return artifact

        service.registry.loader.register_class(service.registry.records[SITE_ID], Site)
        try:
            with pytest.raises(PluginError, match="output_format_values"):
                await service.run("https://example.test/gallery")
        finally:
            await service.close()
        assert not inspected

    asyncio.run(scenario())


def test_missing_plugin_hook_or_key_keeps_the_safe_literal_token(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = _service(
            tmp_path,
            directory_format=f"%CHAPTER_NUMBER%_%PLUGIN[{SITE_ID}:MISSING]%",
            filename_format="%IMAGE_INDEX%%PLUGIN[com.example.absent:FILTER_NAME]%.%EXT%",
        )

        class Site:
            def validate_config(self, _config: object, _app_settings: object) -> None:
                return None

            def matches(self, _url: str) -> bool:
                return True

            async def inspect(self, _url: str, _context: object) -> DownloadManifest:
                return DownloadManifest("Book", (Chapter(1, "One", images=(ImageResource("image:1"),)),))

            async def create_image_request(self, _image: ImageResource, _context: object) -> RequestSpec:
                return RequestSpec("https://example.test/image")

            async def recover_image_request(self, *_args: object) -> None:
                return None

            def auth_flow(self, _context: object) -> None:
                return None

            async def transform_image(self, artifact: ImageArtifact, _context: object) -> ImageArtifact:
                return artifact

        service.registry.loader.register_class(service.registry.records[SITE_ID], Site)
        await service.gateway.client.aclose()
        service.gateway.client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, headers={"content-type": "image/png"}, content=_png(), request=request
                )
            )
        )
        try:
            result = await service.run("https://example.test/gallery")
        finally:
            await service.close()

        saved = Path(result.chapters[0].outcomes[0].path or "")
        assert saved.parent.name == f"0001_%PLUGIN[{SITE_ID.replace('.', '．')}：MISSING]%"
        assert saved.name == "0001%PLUGIN[com．example．absent：FILTER_NAME]%.png"

    asyncio.run(scenario())


@pytest.mark.parametrize("values", ({"filter_name": "bad"}, {"FILTER_NAME": 1}, ["not-a-mapping"]))
def test_invalid_output_format_values_fail_before_inspection(tmp_path: Path, values: object) -> None:
    async def scenario() -> None:
        service = _service(tmp_path)
        inspected = False

        class Site:
            def validate_config(self, _config: object, _app_settings: object) -> None:
                return None

            def matches(self, _url: str) -> bool:
                return True

            def output_format_values(self, _context: object) -> object:
                return values

            async def inspect(self, _url: str, _context: object) -> DownloadManifest:
                nonlocal inspected
                inspected = True
                return DownloadManifest("Book", ())

            async def create_image_request(self, *_args: object) -> RequestSpec:
                raise AssertionError("inspection must not run")

            async def recover_image_request(self, *_args: object) -> None:
                return None

            def auth_flow(self, _context: object) -> None:
                return None

            async def transform_image(self, artifact: ImageArtifact, _context: object) -> ImageArtifact:
                return artifact

        service.registry.loader.register_class(service.registry.records[SITE_ID], Site)
        try:
            with pytest.raises(PluginError, match="output_format_values"):
                await service.run("https://example.test/gallery")
        finally:
            await service.close()
        assert not inspected

    asyncio.run(scenario())
