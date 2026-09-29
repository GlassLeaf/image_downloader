from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

import image_downloader.application.service as service_module
from image_downloader.config import AppConfig, resolve_paths
from image_downloader.models import (
    Chapter,
    DownloadManifest,
    ImageFetchRequest,
    ImageResource,
    RequestResponse,
    RequestSpec,
)
from image_downloader.runtime import RuntimeComposer
from image_downloader.transport.gateway import RequestGateway


def _service(tmp_path: Path):
    plugin_root = (tmp_path / "plugins").resolve()
    plugin_root.mkdir()
    config = AppConfig.model_validate(
        {
            "storage": {"data_root": str((tmp_path / "data").resolve())},
            "plugins": {"root": str(plugin_root)},
            "security": {"plugin_verification": "off"},
            "logging": {"console": {"enabled": False}},
            "notification": {"enabled": False},
        }
    )
    return RuntimeComposer(config, config_root=tmp_path.resolve(), plugin_root=plugin_root).compose()


def test_inspection_composition_does_not_create_a_debug_log(tmp_path: Path) -> None:
    plugin_root = (tmp_path / "plugins").resolve()
    plugin_root.mkdir()
    config = AppConfig.model_validate(
        {
            "storage": {"data_root": str((tmp_path / "data").resolve())},
            "plugins": {"root": str(plugin_root)},
            "security": {"plugin_verification": "off"},
            "logging": {"console": {"enabled": False}},
            "notification": {"enabled": False},
        }
    )

    async def scenario() -> None:
        composer = RuntimeComposer(config, config_root=tmp_path.resolve(), plugin_root=plugin_root)
        service = composer._compose_for_inspection()
        try:
            assert not (resolve_paths(config)["logs"] / "debug.log").exists()
        finally:
            await service.close()

    asyncio.run(scenario())


def test_inspection_resolves_requests_in_manifest_order_and_records_partial_failures(tmp_path: Path) -> None:  # noqa: C901
    class Flow:
        allowed_origins = ("https://cdn.example.test",)

        def __init__(self) -> None:
            self.apply_calls: list[str] = []

        def is_auth_failure(self, _request: RequestSpec, _response: RequestResponse) -> bool:
            raise AssertionError("inspection must not classify an image response")

        async def apply(self, request: RequestSpec) -> RequestSpec:
            self.apply_calls.append(request.url)
            return replace(request, headers={**request.headers, "Authorization": "Bearer inspection"})

        async def refresh(self, _failed: RequestSpec, _response: RequestResponse) -> RequestSpec | None:
            raise AssertionError("inspection must not refresh authentication")

    class Plugin:
        def __init__(self) -> None:
            self.calls: list[str] = []
            self.cleanup_calls = 0
            self.flow = Flow()

        def auth_flow(self, _context: object) -> Flow:
            return self.flow

        async def inspect(self, _url: str, _context: object) -> DownloadManifest:
            return DownloadManifest(
                "Book",
                (
                    Chapter(3, "Three", images=(ImageResource("image:third", image_id="3"),)),
                    Chapter(
                        8,
                        "Eight",
                        images=(ImageResource("image:bad", image_id="bad"), ImageResource("image:last", image_id="9")),
                    ),
                ),
                metadata={"public": "yes"},
            )

        async def create_image_request(self, image: ImageResource, _context: object):
            self.calls.append(image.url)
            if image.image_id == "bad":
                raise ValueError("unavailable")
            if image.image_id == "3":
                return RequestSpec("https://cdn.example.test/3", headers={"X-Token": "raw"})
            return ImageFetchRequest(
                RequestSpec("https://cdn.example.test/9", cookies={"session": "raw"}),
                {"key": "raw"},
            )

        async def recover_image_request(self, *_args: object) -> None:
            raise AssertionError("inspection must not recover image requests")

        async def transform_image(self, *_args: object) -> None:
            raise AssertionError("inspection must not transform images")

        def cleanup_after_use(self) -> None:
            self.cleanup_calls += 1

    async def scenario() -> None:
        service = _service(tmp_path)
        plugin = Plugin()
        record = service.registry.records["core.generic-html"]
        service.registry.resolve = lambda *_args, **_kwargs: (record, plugin)  # type: ignore[method-assign]
        observed_events: list[object] = []
        for event in service.events._handlers:
            service.events.on(event, lambda payload: observed_events.append(payload))
        try:
            result = await service.inspect("https://example.test/gallery")
            assert plugin.calls == ["image:third", "image:bad", "image:last"]
            assert result.request_resolution_performed is True
            assert result.manifest.metadata == {"public": "yes", "source_url": "https://example.test/gallery"}
            positions = [
                (item.chapter_position, item.image_position, item.status.value) for item in result.image_requests
            ]
            assert positions == [
                (1, 1, "resolved"),
                (2, 1, "failed"),
                (2, 2, "resolved"),
            ]
            assert result.image_requests[0].request is not None
            assert result.image_requests[0].request.headers == {"X-Token": "raw"}
            assert result.image_requests[0].uses_image_fetch_request is False
            assert result.image_requests[0].effective_request is not None
            assert result.image_requests[0].effective_request.url == "https://cdn.example.test/3"
            assert ("authorization", "Bearer inspection") in {
                (header.name.lower(), header.value) for header in result.image_requests[0].effective_request.headers
            }
            assert result.image_requests[2].plugin_data == {"key": "raw"}
            assert result.image_requests[2].uses_image_fetch_request is True
            assert result.image_requests[2].effective_request is not None
            assert plugin.flow.apply_calls == ["https://cdn.example.test/3", "https://cdn.example.test/9"]
            assert result.failures[0].failure is not None
            assert result.failures[0].failure.code == "plugin_error"
            assert result.failures[0].failure.phase == "create_image_request"
            assert observed_events == []
            assert plugin.cleanup_calls == 1
        finally:
            await service.close()

    asyncio.run(scenario())


def test_manifest_only_skips_request_hooks_and_inspection_cookie_changes_are_discarded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Plugin:
        def __init__(self) -> None:
            self.request_calls = 0

        def auth_flow(self, _context: object) -> None:
            return None

        async def inspect(self, _url: str, _context: object) -> DownloadManifest:
            return DownloadManifest("Book", (Chapter(1, "One", images=(ImageResource("image:1"),)),))

        async def create_image_request(self, _image: ImageResource, context: object) -> RequestSpec:
            self.request_calls += 1
            response = await context.requests.execute(RequestSpec("https://api.example.test/mint"))
            assert response.status == 200
            return RequestSpec("https://cdn.example.test/1")

        async def recover_image_request(self, *_args: object) -> None:
            raise AssertionError("not called")

        async def transform_image(self, *_args: object) -> None:
            raise AssertionError("not called")

    class InspectionGateway(RequestGateway):
        seen: list[str] = []

        async def _transport(self, spec: RequestSpec, **_kwargs: object) -> RequestResponse:
            type(self).seen.append(spec.url)
            self.client.cookies.set("temporary", "value", domain="example.test")
            return RequestResponse(spec.url, 200, {}, b"ok")

    async def scenario() -> None:
        service = _service(tmp_path)
        plugin = Plugin()
        record = service.registry.records["core.generic-html"]
        service.registry.resolve = lambda *_args, **_kwargs: (record, plugin)  # type: ignore[method-assign]
        original = service_module.RequestGateway
        monkeypatch.setattr(service_module, "RequestGateway", InspectionGateway)
        debug_path = resolve_paths(service.config)["logs"] / "debug.log"
        debug_before = debug_path.read_bytes() if debug_path.exists() else None
        try:
            manifest_only = await service.inspect("https://example.test/gallery", resolve_image_requests=False)
            assert manifest_only.request_resolution_performed is False
            assert manifest_only.image_requests == ()
            assert plugin.request_calls == 0

            result = await service.inspect("https://example.test/gallery")
            assert len(result.image_requests) == 1
            assert plugin.request_calls == 1
            assert InspectionGateway.seen == ["https://api.example.test/mint"]
            assert {cookie.name for cookie in service.gateway.client.cookies.jar} == set()
            if debug_before is None:
                assert not debug_path.exists()
            else:
                assert debug_path.read_bytes() == debug_before
        finally:
            monkeypatch.setattr(service_module, "RequestGateway", original)
            await service.close()

    asyncio.run(scenario())


def test_inspection_previews_the_effective_request_without_sending_an_image(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Flow:
        def is_auth_failure(self, _request: RequestSpec, _response: RequestResponse) -> bool:
            raise AssertionError("inspection must not classify an image response")

        async def apply(self, request: RequestSpec) -> RequestSpec:
            return replace(request, headers={**request.headers, "Authorization": "Bearer inspection"})

        async def refresh(self, _failed: RequestSpec, _response: RequestResponse) -> RequestSpec | None:
            raise AssertionError("inspection must not refresh authentication")

    class Plugin:
        def auth_flow(self, _context: object) -> Flow:
            return Flow()

        async def inspect(self, _url: str, _context: object) -> DownloadManifest:
            return DownloadManifest(
                "Book",
                (Chapter(1, "One", images=(ImageResource("image:form"), ImageResource("image:json"))),),
            )

        async def create_image_request(self, image: ImageResource, _context: object) -> RequestSpec:
            if image.url == "image:json":
                return RequestSpec(
                    "https://example.test/json",
                    method="POST",
                    headers={"X-Plugin": "plugin"},
                    json={"field": "json value"},
                )
            return RequestSpec(
                "https://example.test/image?source=raw",
                method="POST",
                headers={"X-Plugin": "plugin"},
                cookies={"manual": "cookie"},
                referer="https://example.test/gallery",
                query={"query": "value"},
                form={"field": "form value"},
            )

        async def recover_image_request(self, *_args: object) -> None:
            raise AssertionError("inspection must not recover image requests")

        async def transform_image(self, *_args: object) -> None:
            raise AssertionError("inspection must not transform images")

    class PreviewGateway(RequestGateway):
        async def _send_one_hop(self, *_args: object):
            raise AssertionError("inspection must not send the prepared image request")

    config = AppConfig.model_validate(
        {
            "storage": {"data_root": str((tmp_path / "data").resolve())},
            "plugins": {"root": str((tmp_path / "plugins").resolve())},
            "security": {"plugin_verification": "off"},
            "logging": {"console": {"enabled": False}},
            "notification": {"enabled": False},
            "network": {"headers": {"X-Default": "default"}},
        }
    )
    plugin_root = (tmp_path / "plugins").resolve()
    plugin_root.mkdir()

    async def scenario() -> None:
        service = RuntimeComposer(config, config_root=tmp_path.resolve(), plugin_root=plugin_root).compose()
        plugin = Plugin()
        record = service.registry.records["core.generic-html"]
        service.registry.resolve = lambda *_args, **_kwargs: (record, plugin)  # type: ignore[method-assign]
        service.gateway.client.cookies.set("jar", "cookie", domain="example.test")
        service._cookie_baseline = service.cookie_store.snapshot(service.gateway.client.cookies.jar)
        original = service_module.RequestGateway
        monkeypatch.setattr(service_module, "RequestGateway", PreviewGateway)
        try:
            result = await service.inspect("https://example.test/gallery")
            resolution, json_resolution = result.image_requests
            preview = resolution.effective_request
            assert preview is not None
            assert preview.method == "POST"
            assert preview.url == "https://example.test/image?query=value"
            headers = [(header.name.lower(), header.value) for header in preview.headers]
            assert ("x-default", "default") in headers
            assert ("x-plugin", "plugin") in headers
            assert ("authorization", "Bearer inspection") in headers
            assert ("referer", "https://example.test/gallery") in headers
            assert ("content-type", "application/x-www-form-urlencoded") in headers
            assert ("content-length", str(len(b"field=form+value"))) in headers
            assert {(cookie.name, cookie.value) for cookie in preview.cookies} == {
                ("jar", "cookie"),
                ("manual", "cookie"),
            }
            assert preview.body == b"field=form+value"
            json_preview = json_resolution.effective_request
            assert json_preview is not None
            assert json_preview.url == "https://example.test/json"
            assert ("content-type", "application/json") in {
                (header.name.lower(), header.value) for header in json_preview.headers
            }
            assert json_preview.body == b'{"field":"json value"}'
            assert {cookie.name for cookie in service.gateway.client.cookies.jar} == {"jar"}
        finally:
            monkeypatch.setattr(service_module, "RequestGateway", original)
            await service.close()

    asyncio.run(scenario())


def test_inspection_keeps_a_source_request_when_effective_request_preparation_fails(tmp_path: Path) -> None:
    class Flow:
        def is_auth_failure(self, _request: RequestSpec, _response: RequestResponse) -> bool:
            raise AssertionError("inspection must not classify an image response")

        async def apply(self, request: RequestSpec) -> RequestSpec:
            if request.url.endswith("bad"):
                raise ValueError("token unavailable")
            return request

        async def refresh(self, _failed: RequestSpec, _response: RequestResponse) -> RequestSpec | None:
            raise AssertionError("inspection must not refresh authentication")

    class Plugin:
        def auth_flow(self, _context: object) -> Flow:
            return Flow()

        async def inspect(self, _url: str, _context: object) -> DownloadManifest:
            return DownloadManifest(
                "Book",
                (Chapter(1, "One", images=(ImageResource("image:bad"), ImageResource("image:good"))),),
            )

        async def create_image_request(self, image: ImageResource, _context: object) -> RequestSpec:
            return RequestSpec(f"https://example.test/{image.url.removeprefix('image:')}")

        async def recover_image_request(self, *_args: object) -> None:
            raise AssertionError("inspection must not recover image requests")

        async def transform_image(self, *_args: object) -> None:
            raise AssertionError("inspection must not transform images")

    async def scenario() -> None:
        service = _service(tmp_path)
        plugin = Plugin()
        record = service.registry.records["core.generic-html"]
        service.registry.resolve = lambda *_args, **_kwargs: (record, plugin)  # type: ignore[method-assign]
        try:
            result = await service.inspect("https://example.test/gallery")
            failed, resolved = result.image_requests
            assert failed.status.value == "partially_resolved"
            assert failed.request == RequestSpec("https://example.test/bad")
            assert failed.effective_request is None
            assert failed.failure is not None
            assert failed.failure.phase == "effective_request"
            assert resolved.status.value == "resolved"
            assert resolved.effective_request is not None
            assert resolved.effective_request.url == "https://example.test/good"
            assert result.failures == (failed,)
        finally:
            await service.close()

    asyncio.run(scenario())
