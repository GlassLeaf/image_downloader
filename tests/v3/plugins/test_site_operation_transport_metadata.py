from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from PIL import Image
from test_plugin_v3 import make_plugin

from image_downloader.config import AppConfig
from image_downloader.exceptions import AuthenticationError, ConfigurationError, PluginError
from image_downloader.models import (
    Chapter,
    DownloadManifest,
    ImageArtifact,
    ImageFetchRequest,
    ImageResource,
    RequestSpec,
    UpdateSnapshot,
)
from image_downloader.runtime import DownloadService, RuntimeComposer

SITE_ID = "com.example.gallery"
ALLOWED_PROCESSOR_ID = "com.example.allowed"
BLOCKED_PROCESSOR_ID = "com.example.blocked"


def _service(
    tmp_path: Path,
    *,
    processors: tuple[str, ...] = (),
    additional_site_ids: tuple[str, ...] = (),
    transport_metadata_access: dict[str, list[str]] | None = None,
    allow_empty_manifest: bool = False,
    network: dict[str, object] | None = None,
    filename_format: str | None = None,
) -> DownloadService:
    root = (tmp_path / "plugins").resolve()
    make_plugin(root, plugin_id=SITE_ID)
    for site_id in additional_site_ids:
        make_plugin(root, plugin_id=site_id)
    for processor_id in processors:
        make_plugin(root, plugin_id=processor_id, kind="image_processor_plugin")
    config = AppConfig.model_validate(
        {
            "storage": {"data_root": str((tmp_path / "data").resolve())},
            "plugins": {"root": str(root)},
            "security": {"plugin_verification": "off"},
            "download": {"allow_empty_chapter_manifest": allow_empty_manifest},
            "network": network or {},
            "image_processors": {
                "chain": list(processors),
                "transport_metadata_access": transport_metadata_access or {},
            },
            "output": {
                "existing_file": "overwrite",
                **({"filename_format": filename_format} if filename_format else {}),
            },
            "logging": {"console": {"enabled": False}},
            "notification": {"enabled": False},
        }
    )
    return RuntimeComposer(config, config_root=tmp_path.resolve(), plugin_root=root).compose()


def _png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (2, 2), "blue").save(output, format="PNG")
    return output.getvalue()


def test_image_fetch_request_is_immutable_and_validates_plugin_data() -> None:
    request = ImageFetchRequest(RequestSpec("https://example.test/image"), {"variant": "full"})

    assert request.plugin_data == {"variant": "full"}
    with pytest.raises(TypeError):
        request.plugin_data["variant"] = "thumbnail"  # type: ignore[index]
    with pytest.raises(TypeError):
        ImageFetchRequest(RequestSpec("https://example.test/image"), {"variant": 1})  # type: ignore[arg-type]


def test_transport_metadata_access_requires_a_chain_processor_and_nonbuiltin_site(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="site IDs are invalid or duplicated"):
        AppConfig.model_validate(
            {
                "image_processors": {
                    "transport_metadata_access": {ALLOWED_PROCESSOR_ID: [SITE_ID, SITE_ID]},
                }
            }
        )
    with pytest.raises(ConfigurationError, match="processor is unavailable"):
        _service(
            tmp_path / "missing-processor",
            transport_metadata_access={ALLOWED_PROCESSOR_ID: [SITE_ID]},
        )
    with pytest.raises(ConfigurationError, match="site plugin is unavailable"):
        _service(
            tmp_path / "builtin-site",
            processors=(ALLOWED_PROCESSOR_ID,),
            transport_metadata_access={ALLOWED_PROCESSOR_ID: ["core.generic-html"]},
        )


def test_transport_metadata_reaches_site_and_only_authorized_processors(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = _service(
            tmp_path,
            processors=(ALLOWED_PROCESSOR_ID, BLOCKED_PROCESSOR_ID),
            transport_metadata_access={ALLOWED_PROCESSOR_ID: [SITE_ID]},
        )
        observed: dict[str, object] = {}

        class Site:
            def validate_config(self, _config: object, _app_settings: object) -> None:
                return None

            def matches(self, _url: str) -> bool:
                return True

            async def inspect(self, _url: str, _context: object) -> DownloadManifest:
                return DownloadManifest("Book", (Chapter(1, "One", images=(ImageResource("image:42"),)),))

            async def create_image_request(self, _image: ImageResource, _context: object) -> ImageFetchRequest:
                return ImageFetchRequest(
                    RequestSpec(
                        "https://example.test/image?token=response-secret",
                        headers={"X-Request": "request-secret"},
                        cookies={"manual": "cookie-secret"},
                    ),
                    {"key": "plugin-secret"},
                )

            async def recover_image_request(self, *_args: object) -> None:
                return None

            def auth_flow(self, _context: object) -> None:
                return None

            async def transform_image(self, artifact: ImageArtifact, context: object) -> ImageArtifact:
                observed["site"] = context.transport_metadata  # type: ignore[attr-defined]
                return artifact

        class AllowedProcessor:
            def validate_config(self, _config: object, _app_settings: object) -> None:
                return None

            async def transform(self, artifact: ImageArtifact, context: object) -> ImageArtifact:
                observed["allowed"] = context.transport_metadata  # type: ignore[attr-defined]
                return artifact

        class BlockedProcessor(AllowedProcessor):
            async def transform(self, artifact: ImageArtifact, context: object) -> ImageArtifact:
                observed["blocked"] = context.transport_metadata  # type: ignore[attr-defined]
                return artifact

        service.registry.loader.register_class(service.registry.records[SITE_ID], Site)
        service.registry.loader.register_class(service.registry.records[ALLOWED_PROCESSOR_ID], AllowedProcessor)
        service.registry.loader.register_class(service.registry.records[BLOCKED_PROCESSOR_ID], BlockedProcessor)
        await service.gateway.client.aclose()

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/image"
            return httpx.Response(
                200,
                headers={"content-type": "image/png", "X-Response": "response-secret"},
                content=_png(),
                request=request,
            )

        service.gateway.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
        try:
            result = await service.run("https://example.test/gallery")
            assert len(result.saved_files) == 1
            site = observed["site"]
            allowed = observed["allowed"]
            blocked = observed["blocked"]
            assert site is allowed
            assert not site.is_redacted  # type: ignore[union-attr]
            assert site.initial_request.url.endswith("token=response-secret")  # type: ignore[union-attr]
            assert site.initial_request.headers["x-request"] == ("request-secret",)  # type: ignore[union-attr]
            assert site.initial_request.cookies[0].value == "cookie-secret"  # type: ignore[union-attr]
            assert site.response_headers["x-response"] == ("response-secret",)  # type: ignore[union-attr]
            assert site.plugin_data == {"key": "plugin-secret"}  # type: ignore[union-attr]
            assert blocked.is_redacted  # type: ignore[union-attr]
            assert "response-secret" not in blocked.response_url  # type: ignore[union-attr]
            assert blocked.initial_request.headers["x-request"] == ("[REDACTED]",)  # type: ignore[union-attr]
            assert blocked.initial_request.cookies[0].value == "[REDACTED]"  # type: ignore[union-attr]
            assert blocked.plugin_data == {"key": "[REDACTED]"}  # type: ignore[union-attr]
            assert "response-secret" not in str(result)
            debug_log = (service.logs.root / "debug.log").read_text(encoding="utf-8")
            assert "response-secret" not in debug_log
            assert "request-secret" not in debug_log
            assert "cookie-secret" not in debug_log
            assert "plugin-secret" not in debug_log
        finally:
            await service.close()

    asyncio.run(scenario())


def test_site_cleanup_after_use_is_optional_and_preserves_primary_failure(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = _service(tmp_path, allow_empty_manifest=True)
        calls: list[str] = []

        class Site:
            def validate_config(self, _config: object, _app_settings: object) -> None:
                return None

            def matches(self, _url: str) -> bool:
                return True

            async def inspect(self, _url: str, _context: object) -> DownloadManifest:
                calls.append("inspect")
                return DownloadManifest("Book", ())

            async def create_image_request(self, *_args: object) -> RequestSpec:
                raise AssertionError("not used")

            async def recover_image_request(self, *_args: object) -> None:
                return None

            def auth_flow(self, _context: object) -> None:
                return None

            async def transform_image(self, artifact: ImageArtifact, _context: object) -> ImageArtifact:
                return artifact

            async def cleanup_after_use(self) -> None:
                calls.append("cleanup")

        service.registry.loader.register_class(service.registry.records[SITE_ID], Site)
        try:
            await service.run("https://example.test/gallery")
            assert calls == ["inspect", "cleanup"]
        finally:
            await service.close()

        failing = _service(tmp_path / "failure", allow_empty_manifest=True)
        failed_calls: list[str] = []

        class FailingSite(Site):
            async def inspect(self, _url: str, _context: object) -> DownloadManifest:
                raise PluginError("primary failure")

            async def cleanup_after_use(self) -> None:
                failed_calls.append("closed")
                raise RuntimeError("cleanup failure")

        failing.registry.loader.register_class(failing.registry.records[SITE_ID], FailingSite)
        try:
            with pytest.raises(PluginError, match="primary failure"):
                await failing.run("https://example.test/gallery")
            assert failed_calls == ["closed"]
            assert "event=site_operation_close_failed" in (
                failing.logs.root / "debug.log"
            ).read_text(encoding="utf-8")
        finally:
            await failing.close()

    asyncio.run(scenario())


def test_site_cleanup_failure_is_plugin_error_on_normal_completion(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = _service(tmp_path, allow_empty_manifest=True)

        class Site:
            def validate_config(self, _config: object, _app_settings: object) -> None:
                return None

            def matches(self, _url: str) -> bool:
                return True

            async def inspect(self, _url: str, _context: object) -> DownloadManifest:
                return DownloadManifest("Book", ())

            async def create_image_request(self, *_args: object) -> RequestSpec:
                raise AssertionError("not used")

            async def recover_image_request(self, *_args: object) -> None:
                return None

            def auth_flow(self, _context: object) -> None:
                return None

            async def transform_image(self, artifact: ImageArtifact, _context: object) -> ImageArtifact:
                return artifact

            async def cleanup_after_use(self) -> None:
                raise AuthenticationError("not an operation authentication failure")

        service.registry.loader.register_class(service.registry.records[SITE_ID], Site)
        try:
            with pytest.raises(PluginError, match="hook=cleanup_after_use"):
                await service.run("https://example.test/gallery")
        finally:
            await service.close()

    asyncio.run(scenario())


def test_site_cleanup_runs_after_update_check(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = _service(tmp_path)
        calls: list[str] = []

        class Site:
            def validate_config(self, _config: object, _app_settings: object) -> None:
                return None

            def matches(self, _url: str) -> bool:
                return True

            async def inspect(self, _url: str, _context: object) -> DownloadManifest:
                return DownloadManifest("Book", ())

            async def create_image_request(self, *_args: object) -> RequestSpec:
                raise AssertionError("not used")

            async def recover_image_request(self, *_args: object) -> None:
                return None

            def auth_flow(self, _context: object) -> None:
                return None

            async def transform_image(self, artifact: ImageArtifact, _context: object) -> ImageArtifact:
                return artifact

            async def check_updates(self, url: str, _context: object) -> UpdateSnapshot:
                return UpdateSnapshot(url, (), datetime.now(UTC))

            def cleanup_after_use(self) -> None:
                calls.append("closed")

        service.registry.loader.register_class(service.registry.records[SITE_ID], Site)
        try:
            assert (await service.check_updates("https://example.test/gallery")).changes == ()
            assert calls == ["closed"]
        finally:
            await service.close()

    asyncio.run(scenario())


def test_site_cleanup_after_use_runs_after_auth_failure(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = _service(tmp_path)
        calls: list[str] = []

        class Flow:
            async def apply(self, _request: RequestSpec) -> RequestSpec:
                raise AuthenticationError("authentication failed")

            async def refresh(self, _failed: RequestSpec, _response: object) -> None:
                return None

            def is_auth_failure(self, _request: RequestSpec, _response: object) -> bool:
                return True

        class Site:
            def validate_config(self, _config: object, _app_settings: object) -> None:
                return None

            def matches(self, _url: str) -> bool:
                return True

            async def inspect(self, _url: str, context: object) -> DownloadManifest:
                await context.requests.execute(RequestSpec("https://example.test/protected"))  # type: ignore[attr-defined]
                raise AssertionError("unreachable")

            async def create_image_request(self, *_args: object) -> RequestSpec:
                raise AssertionError("not used")

            async def recover_image_request(self, *_args: object) -> None:
                return None

            def auth_flow(self, _context: object) -> Flow:
                return Flow()

            async def transform_image(self, artifact: ImageArtifact, _context: object) -> ImageArtifact:
                return artifact

            def cleanup_after_use(self) -> None:
                calls.append("cleanup")

        service.registry.loader.register_class(service.registry.records[SITE_ID], Site)
        try:
            with pytest.raises(AuthenticationError, match="authentication failed"):
                await service.run("https://example.test/gallery")
            assert calls == ["cleanup"]
        finally:
            await service.close()

    asyncio.run(scenario())


def test_site_cleanup_after_use_runs_after_image_transform_failure(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = _service(tmp_path)
        calls: list[str] = []

        class Site:
            def validate_config(self, _config: object, _app_settings: object) -> None:
                return None

            def matches(self, _url: str) -> bool:
                return True

            async def inspect(self, _url: str, _context: object) -> DownloadManifest:
                return DownloadManifest("Book", (Chapter(1, "One", images=(ImageResource("image:1"),)),))

            async def create_image_request(self, *_args: object) -> RequestSpec:
                return RequestSpec("https://example.test/image", auth_required=False)

            async def recover_image_request(self, *_args: object) -> None:
                return None

            def auth_flow(self, _context: object) -> None:
                return None

            async def transform_image(self, _artifact: ImageArtifact, _context: object) -> ImageArtifact:
                raise RuntimeError("transform failed")

            async def cleanup_after_use(self) -> None:
                calls.append("cleanup")

        service.registry.loader.register_class(service.registry.records[SITE_ID], Site)
        await service.gateway.client.aclose()
        service.gateway.client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"content-type": "image/png"},
                    content=_png(),
                    request=request,
                )
            )
        )
        try:
            with pytest.raises(PluginError, match="hook=transform_image"):
                await service.run("https://example.test/gallery")
            assert calls == ["cleanup"]
        finally:
            await service.close()

    asyncio.run(scenario())


def test_site_cleanup_after_use_runs_on_cancellation(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = _service(tmp_path)
        inspected = asyncio.Event()
        calls: list[str] = []

        class Site:
            def validate_config(self, _config: object, _app_settings: object) -> None:
                return None

            def matches(self, _url: str) -> bool:
                return True

            async def inspect(self, _url: str, _context: object) -> DownloadManifest:
                inspected.set()
                await asyncio.Future()
                raise AssertionError("unreachable")

            async def create_image_request(self, *_args: object) -> RequestSpec:
                raise AssertionError("not used")

            async def recover_image_request(self, *_args: object) -> None:
                return None

            def auth_flow(self, _context: object) -> None:
                return None

            async def transform_image(self, artifact: ImageArtifact, _context: object) -> ImageArtifact:
                return artifact

            def cleanup_after_use(self) -> None:
                calls.append("cleanup")

        service.registry.loader.register_class(service.registry.records[SITE_ID], Site)
        try:
            task = asyncio.create_task(service.run("https://example.test/gallery"))
            await asyncio.wait_for(inspected.wait(), timeout=5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert calls == ["cleanup"]
        finally:
            await service.close()

    asyncio.run(scenario())


def test_site_cleanup_after_use_skips_unselected_matcher_instances(tmp_path: Path) -> None:
    async def scenario() -> None:
        unmatched_id = "com.example.unmatched"
        service = _service(tmp_path, additional_site_ids=(unmatched_id,), allow_empty_manifest=True)
        calls: list[str] = []

        class UnmatchedSite:
            def validate_config(self, _config: object, _app_settings: object) -> None:
                return None

            def matches(self, _url: str) -> bool:
                return False

            async def inspect(self, *_args: object) -> DownloadManifest:
                raise AssertionError("not selected")

            async def create_image_request(self, *_args: object) -> RequestSpec:
                raise AssertionError("not selected")

            async def recover_image_request(self, *_args: object) -> None:
                return None

            def auth_flow(self, _context: object) -> None:
                return None

            async def transform_image(self, artifact: ImageArtifact, _context: object) -> ImageArtifact:
                return artifact

            def cleanup_after_use(self) -> None:
                calls.append("unmatched")

        class SelectedSite(UnmatchedSite):
            def matches(self, _url: str) -> bool:
                return True

            async def inspect(self, *_args: object) -> DownloadManifest:
                return DownloadManifest("Book", ())

            def cleanup_after_use(self) -> None:
                calls.append("selected")

        service.registry.loader.register_class(service.registry.records[SITE_ID], SelectedSite)
        service.registry.loader.register_class(service.registry.records[unmatched_id], UnmatchedSite)
        try:
            await service.run("https://example.test/gallery")
            assert calls == ["selected"]
        finally:
            await service.close()

    asyncio.run(scenario())


def test_transport_metadata_tracks_auth_retry_redirect_and_duplicate_response_headers(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = _service(tmp_path, network={"max_attempts": 2, "retry_max_delay_seconds": 0})
        service._persist_cookies = AsyncMock()  # type: ignore[method-assign]
        observed: dict[str, object] = {}
        starts = 0

        class Flow:
            async def apply(self, request: RequestSpec) -> RequestSpec:
                return replace(request, headers={**request.headers, "Authorization": "Bearer transport-secret"})

            async def refresh(self, _failed: RequestSpec, _response: object) -> None:
                return None

            def is_auth_failure(self, _request: RequestSpec, _response: object) -> bool:
                return False

        class Site:
            def validate_config(self, _config: object, _app_settings: object) -> None:
                return None

            def matches(self, _url: str) -> bool:
                return True

            async def inspect(self, _url: str, _context: object) -> DownloadManifest:
                return DownloadManifest("Book", (Chapter(1, "One", images=(ImageResource("image:42"),)),))

            async def create_image_request(self, _image: ImageResource, _context: object) -> ImageFetchRequest:
                return ImageFetchRequest(
                    RequestSpec(
                        "https://example.test/start?initial=one",
                        headers={"X-Request": "header-secret"},
                        cookies={"manual": "cookie-secret"},
                    ),
                    {"phase": "initial"},
                )

            async def recover_image_request(self, *_args: object) -> None:
                return None

            def auth_flow(self, _context: object) -> Flow:
                return Flow()

            async def transform_image(self, artifact: ImageArtifact, context: object) -> ImageArtifact:
                observed["metadata"] = context.transport_metadata  # type: ignore[attr-defined]
                return artifact

        service.registry.loader.register_class(service.registry.records[SITE_ID], Site)
        await service.gateway.client.aclose()

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal starts
            if request.url.path == "/start":
                starts += 1
                if starts == 1:
                    return httpx.Response(503, request=request)
                return httpx.Response(302, headers={"Location": "/final?redirect=two"}, request=request)
            assert request.url.path == "/final"
            return httpx.Response(
                200,
                headers=[
                    ("content-type", "image/png"),
                    ("set-cookie", "first=one"),
                    ("set-cookie", "second=two"),
                ],
                content=_png(),
                request=request,
            )

        service.gateway.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
        try:
            await service.run("https://example.test/gallery")
            assert starts == 2
            metadata = observed["metadata"]
            assert metadata.initial_request.url.endswith("initial=one")  # type: ignore[union-attr]
            assert metadata.final_request.url.endswith("redirect=two")  # type: ignore[union-attr]
            assert metadata.initial_request.headers["authorization"] == ("Bearer transport-secret",)  # type: ignore[union-attr]
            assert len(metadata.initial_request.cookies) == 1  # type: ignore[union-attr]
            assert metadata.initial_request.cookies[0].name == "manual"  # type: ignore[union-attr]
            assert metadata.initial_request.cookies[0].value == "cookie-secret"  # type: ignore[union-attr]
            assert metadata.response_headers["set-cookie"] == ("first=one", "second=two")  # type: ignore[union-attr]
        finally:
            await service.close()

    asyncio.run(scenario())


def test_recovery_bare_request_keeps_plugin_data_and_dto_replaces_it(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = _service(tmp_path)
        observed: dict[str, object] = {}

        class Site:
            def validate_config(self, _config: object, _app_settings: object) -> None:
                return None

            def matches(self, _url: str) -> bool:
                return True

            async def inspect(self, _url: str, _context: object) -> DownloadManifest:
                return DownloadManifest(
                    "Book",
                    (
                        Chapter(
                            1,
                            "One",
                            images=(ImageResource("image:inherit", index=1), ImageResource("image:replace", index=2)),
                        ),
                    ),
                )

            async def create_image_request(self, image: ImageResource, _context: object) -> ImageFetchRequest:
                return ImageFetchRequest(
                    RequestSpec(f"https://example.test/initial/{image.index}", auth_required=False),
                    {"phase": "initial"},
                )

            async def recover_image_request(
                self, image: ImageResource, _failed: RequestSpec, _response: object, _context: object
            ) -> RequestSpec | ImageFetchRequest:
                if image.index == 1:
                    return RequestSpec("https://example.test/recovered/inherit", auth_required=False)
                return ImageFetchRequest(
                    RequestSpec("https://example.test/recovered/replace", auth_required=False),
                    {"phase": "replacement"},
                )

            def auth_flow(self, _context: object) -> None:
                return None

            async def transform_image(self, artifact: ImageArtifact, context: object) -> ImageArtifact:
                observed[artifact.source_url] = context.transport_metadata  # type: ignore[attr-defined]
                return artifact

        service.registry.loader.register_class(service.registry.records[SITE_ID], Site)
        await service.gateway.client.aclose()

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.startswith("/initial/"):
                return httpx.Response(403, request=request)
            return httpx.Response(200, headers={"content-type": "image/png"}, content=_png(), request=request)

        service.gateway.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            await service.run("https://example.test/gallery")
            assert observed["image:inherit"].plugin_data == {"phase": "initial"}  # type: ignore[union-attr]
            assert observed["image:replace"].plugin_data == {"phase": "replacement"}  # type: ignore[union-attr]
            assert observed["image:inherit"].initial_request.url.endswith("/recovered/inherit")  # type: ignore[union-attr]
            assert observed["image:replace"].final_request.url.endswith("/recovered/replace")  # type: ignore[union-attr]
        finally:
            await service.close()

    asyncio.run(scenario())


def test_manifest_original_filename_survives_an_artifact_replacement(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = _service(tmp_path, filename_format="%ORIGINAL_STEM%.%EXT%")

        class Site:
            def validate_config(self, _config: object, _app_settings: object) -> None:
                return None

            def matches(self, _url: str) -> bool:
                return True

            async def inspect(self, _url: str, _context: object) -> DownloadManifest:
                return DownloadManifest(
                    "Book",
                    (Chapter(1, "One", images=(ImageResource("image:1", original_filename="logical.webp"),)),),
                )

            async def create_image_request(self, _image: ImageResource, _context: object) -> RequestSpec:
                return RequestSpec("https://example.test/server-name.webp", auth_required=False)

            async def recover_image_request(self, *_args: object) -> None:
                return None

            def auth_flow(self, _context: object) -> None:
                return None

            async def transform_image(self, artifact: ImageArtifact, _context: object) -> ImageArtifact:
                return ImageArtifact(artifact.data, artifact.content_type, artifact.source_url)

        service.registry.loader.register_class(service.registry.records[SITE_ID], Site)
        await service.gateway.client.aclose()
        service.gateway.client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"content-type": "image/png", "content-disposition": "attachment; filename=header.webp"},
                    content=_png(),
                    request=request,
                )
            )
        )
        try:
            result = await service.run("https://example.test/gallery")
            assert [Path(path).name for path in result.saved_files] == ["logical.png"]
        finally:
            await service.close()

    asyncio.run(scenario())


def test_generic_html_uses_its_img_source_url_when_no_higher_priority_name_exists(tmp_path: Path) -> None:
    async def scenario() -> None:
        plugin_root = (tmp_path / "plugins").resolve()
        config = AppConfig.model_validate(
            {
                "storage": {"data_root": str((tmp_path / "data").resolve())},
                "plugins": {"root": str(plugin_root)},
                "security": {"plugin_verification": "off"},
                "output": {"filename_format": "%ORIGINAL_STEM%.%EXT%"},
                "logging": {"console": {"enabled": False}},
                "notification": {"enabled": False},
            }
        )
        service = RuntimeComposer(config, config_root=tmp_path.resolve(), plugin_root=plugin_root).compose()
        await service.gateway.client.aclose()

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/gallery":
                return httpx.Response(
                    200,
                    content=b'<html><img src="/images/original_file.webp"></html>',
                    request=request,
                )
            assert request.url.path == "/images/original_file.webp"
            return httpx.Response(200, headers={"content-type": "image/png"}, content=_png(), request=request)

        service.gateway.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            result = await service.run("https://example.test/gallery")
            assert [Path(path).name for path in result.saved_files] == ["original_file.png"]
        finally:
            await service.close()

    asyncio.run(scenario())
