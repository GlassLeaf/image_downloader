from __future__ import annotations

import asyncio
import io
from concurrent.futures.process import BrokenProcessPool
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from PIL import Image

from image_downloader import OriginScopedAuthFlow
from image_downloader.config import AppConfig
from image_downloader.exceptions import AuthenticationError, DownloaderError, PluginError
from image_downloader.media import ImageProcessor
from image_downloader.models import (
    Chapter,
    RequestResponse,
    RequestSpec,
    UpdateCandidate,
    UpdateChangeKind,
    UpdateSnapshot,
)
from image_downloader.observability.events import EventName
from image_downloader.observability.logging import safe_log_text
from image_downloader.runtime import DownloadService, OutputAllocator, RequestGateway, RuntimeComposer, UpdateState
from image_downloader.storage import FileSystem, safe_component


def _config(**network: object) -> AppConfig:
    return AppConfig.model_validate({"network": network})


def _png(width: int, height: int) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(output, format="PNG")
    return output.getvalue()


def test_response_byte_limit_checks_declared_and_streamed_lengths() -> None:
    async def scenario() -> None:
        config = _config(max_response_bytes=4)
        gateway = RequestGateway(config)
        await gateway.client.aclose()
        gateway.client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"12345", request=request))
        )
        try:
            with pytest.raises(DownloaderError, match="byte limit"):
                await gateway.execute(RequestSpec("https://example.test/image.png", auth_required=False))
        finally:
            await gateway.close()

    asyncio.run(scenario())


def test_non_idempotent_requests_retry_only_when_explicitly_enabled() -> None:
    async def scenario() -> None:
        gateway = RequestGateway(_config(max_attempts=3, retry_max_delay_seconds=0.001))
        calls = 0

        async def unavailable(
            spec: RequestSpec, *, allowed_redirect_origins: frozenset[str] | None = None
        ) -> RequestResponse:
            assert allowed_redirect_origins is None
            nonlocal calls
            calls += 1
            return RequestResponse(spec.url, 503, {}, b"")

        gateway._once = unavailable  # type: ignore[method-assign]
        response = await gateway._transport(
            RequestSpec("https://example.test/action", method="POST", auth_required=False)
        )
        assert response.status == 503
        assert calls == 1

        calls = 0
        response = await gateway._transport(
            RequestSpec(
                "https://example.test/action",
                method="POST",
                auth_required=False,
                retry_non_idempotent=True,
            )
        )
        assert response.status == 503
        assert calls == 3
        await gateway.close()

    asyncio.run(scenario())


def test_authentication_is_scoped_to_the_operation_origin() -> None:
    class Flow:
        allowed_origins = ("https://cdn.example.test",)

        async def apply(self, request: RequestSpec) -> RequestSpec:
            return request

        async def refresh(self, request: RequestSpec, response: RequestResponse) -> RequestSpec | None:
            return request

        def is_auth_failure(self, request: RequestSpec, response: RequestResponse) -> bool:
            return False

    async def scenario() -> None:
        gateway = RequestGateway(AppConfig())
        await gateway.client.aclose()
        gateway.client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"ok", request=request))
        )
        flow = Flow()
        assert isinstance(flow, OriginScopedAuthFlow)
        session = gateway.operation(
            plugin_id="com.example.gallery",
            operation_url="https://example.test/gallery",
            auth_flow_factory=lambda _: flow,
        )
        try:
            assert (await session.execute(RequestSpec("https://cdn.example.test/image.png"))).body == b"ok"
            with pytest.raises(AuthenticationError, match="outside the configured origins"):
                await session.execute(RequestSpec("https://attacker.test/image.png"))
        finally:
            await gateway.close()

    asyncio.run(scenario())


def test_auth_flow_without_allowed_origins_remains_compatible() -> None:
    class Flow:
        async def apply(self, request: RequestSpec) -> RequestSpec:
            return request

        async def refresh(self, request: RequestSpec, response: RequestResponse) -> RequestSpec | None:
            return request

        def is_auth_failure(self, request: RequestSpec, response: RequestResponse) -> bool:
            return False

    async def scenario() -> None:
        gateway = RequestGateway(AppConfig())
        flow = Flow()
        assert not isinstance(flow, OriginScopedAuthFlow)
        gateway.operation(
            plugin_id="com.example.gallery",
            operation_url="https://example.test/gallery",
            auth_flow_factory=lambda _: flow,
        )
        await gateway.close()

    asyncio.run(scenario())


def test_auth_flow_boundary_rejects_an_incomplete_implementation() -> None:
    async def scenario() -> None:
        gateway = RequestGateway(AppConfig())
        try:
            with pytest.raises(PluginError, match="must return AuthFlow"):
                gateway.operation(
                    plugin_id="com.example.gallery",
                    operation_url="https://example.test/gallery",
                    auth_flow_factory=lambda _: object(),
                )
        finally:
            await gateway.close()

    asyncio.run(scenario())


def test_host_and_registrable_site_limiters_use_distinct_keys() -> None:
    async def scenario() -> None:
        gateway = RequestGateway(AppConfig())
        await gateway.client.aclose()
        gateway.client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"ok", request=request))
        )
        try:
            await gateway.execute(RequestSpec("https://a.example.com/one", auth_required=False))
            await gateway.execute(RequestSpec("https://b.example.com/two", auth_required=False))
            assert set(gateway._hosts) == {"https://a.example.com:443", "https://b.example.com:443"}
            assert set(gateway._sites) == {"example.com"}
        finally:
            await gateway.close()

    asyncio.run(scenario())


def test_image_pixel_limit_is_disabled_by_default_and_opt_in() -> None:
    data = _png(20, 20)
    original_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = 1
    try:
        with ImageProcessor() as processor:
            assert processor.inspect(data) == "image/png"
            assert Image.MAX_IMAGE_PIXELS == 1
            with pytest.raises(DownloaderError, match="pixel limit"):
                processor.inspect(data, max_pixels=399)
            assert Image.MAX_IMAGE_PIXELS == 1
    finally:
        Image.MAX_IMAGE_PIXELS = original_limit


def test_image_processor_close_is_idempotent_and_rejects_new_work() -> None:
    processor = ImageProcessor()
    assert processor.inspect(_png(1, 1)) == "image/png"
    processor.close()
    processor.close()
    with pytest.raises(DownloaderError, match="image processor is closed"):
        processor.inspect(_png(1, 1))


def test_image_worker_failure_is_reported_as_downloader_error(monkeypatch: pytest.MonkeyPatch) -> None:
    processor = ImageProcessor()

    def fail_submit(*_args: object) -> None:
        raise BrokenProcessPool("worker stopped")

    monkeypatch.setattr(processor._executor, "submit", fail_submit)
    try:
        with pytest.raises(DownloaderError, match="image worker process failed"):
            processor.inspect(_png(1, 1))
    finally:
        processor.close()


def test_filename_truncation_is_disabled_by_default_and_opt_in(tmp_path: Path) -> None:
    title = "a" * 300
    assert safe_component(title) == title

    unlimited = OutputAllocator(FileSystem(tmp_path.resolve()), AppConfig())
    assert unlimited.chapter_directory(Chapter(1, title)).name.endswith(title)

    limited_config = AppConfig.model_validate({"output": {"directory_format": "%TITLE%", "max_component_length": 16}})
    limited = OutputAllocator(FileSystem(tmp_path.resolve()), limited_config).chapter_directory(Chapter(1, title)).name
    assert len(limited) == 16
    assert limited == safe_component(title, max_length=16)


def test_corrupt_update_state_is_not_silently_discarded(tmp_path: Path) -> None:
    filesystem = FileSystem(tmp_path.resolve())
    filesystem.write_bytes_atomic(Path("updates.json"), b"not-json")
    with pytest.raises(DownloaderError, match="cannot be read"):
        UpdateState(filesystem).records()


def test_bounded_scheduler_does_not_create_all_queued_jobs_after_failure() -> None:
    async def scenario() -> None:
        started: list[int] = []

        async def job(index: int) -> int:
            started.append(index)
            if index == 0:
                raise RuntimeError("stop")
            await asyncio.sleep(0)
            return index

        factories = [lambda index=index: job(index) for index in range(20)]
        with pytest.raises(RuntimeError, match="stop"):
            await DownloadService._bounded(None, factories, 2)  # type: ignore[arg-type]
        assert len(started) <= 2

    asyncio.run(scenario())


def test_structured_log_text_removes_control_characters() -> None:
    rendered = safe_log_text("title\nerror: forged\r\nAuthorization: secret")
    assert "\n" not in rendered
    assert "\r" not in rendered
    assert "secret" not in rendered


def test_complete_builtin_download_exercises_streaming_pipeline_and_skip(tmp_path: Path) -> None:
    async def scenario() -> None:
        data_root = (tmp_path / "data").resolve()
        plugin_root = (tmp_path / "plugins").resolve()
        config = AppConfig.model_validate(
            {
                "storage": {"data_root": str(data_root)},
                "plugins": {"root": str(plugin_root)},
                "security": {"plugin_verification": "off"},
                "output": {"existing_file": "skip"},
                "logging": {"console": {"enabled": False}},
                "notification": {"enabled": False},
            }
        )
        service = RuntimeComposer(config, config_root=tmp_path.resolve(), plugin_root=plugin_root).compose()
        await service.gateway.client.aclose()
        image_data = _png(12, 8)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/gallery":
                return httpx.Response(
                    200,
                    headers={"content-type": "text/html"},
                    content=b"<title>Gallery</title><img src='/one.png'>",
                    request=request,
                )
            return httpx.Response(200, headers={"content-type": "image/png"}, content=image_data, request=request)

        service.gateway.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            first = await service.run("https://example.test/gallery")
            second = await service.run("https://example.test/gallery")
            assert len(first.saved_files) == 1
            assert len(second.skipped_files) == 1
            saved = data_root / "profiles" / "default" / "downloads" / "0001_Gallery" / "0001.jpeg"
            assert saved.is_file()
            assert "save:" in saved.with_name("log.log").read_text(encoding="utf-8")
        finally:
            await service.close()

    asyncio.run(scenario())


def test_update_snapshots_report_added_changed_and_removed_entries(tmp_path: Path) -> None:
    class UpdatePlugin:
        def __init__(self) -> None:
            self.snapshots = [
                UpdateSnapshot(
                    "https://example.test/gallery",
                    (
                        UpdateCandidate("https://example.test/a", "a", "1"),
                        UpdateCandidate("https://example.test/b", "b", "1"),
                    ),
                    datetime.now(UTC),
                ),
                UpdateSnapshot(
                    "https://example.test/gallery",
                    (
                        UpdateCandidate("https://example.test/a", "a", "2"),
                        UpdateCandidate("https://example.test/c", "c", "1"),
                    ),
                    datetime.now(UTC),
                ),
            ]

        def auth_flow(self, _context: object) -> None:
            return None

        async def check_updates(self, _url: str, _context: object) -> UpdateSnapshot:
            return self.snapshots.pop(0)

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
        plugin = UpdatePlugin()
        service.registry.resolve = lambda *_args, **_kwargs: (record, plugin)  # type: ignore[method-assign]
        observed: list[EventName] = []
        for event in (EventName.UPDATE_CHECK_STARTED, EventName.UPDATED_URL_FOUND, EventName.UPDATE_CHECK_FINISHED):
            service.events.on(event, lambda _payload, event=event: observed.append(event))
        try:
            first = await service.check_updates("https://example.test/gallery")
            second = await service.check_updates("https://example.test/gallery")
            assert [change.kind for change in first.changes] == [UpdateChangeKind.ADDED, UpdateChangeKind.ADDED]
            assert {change.kind for change in second.changes} == {
                UpdateChangeKind.CHANGED,
                UpdateChangeKind.ADDED,
                UpdateChangeKind.REMOVED,
            }
            assert observed == [
                EventName.UPDATE_CHECK_STARTED,
                EventName.UPDATED_URL_FOUND,
                EventName.UPDATED_URL_FOUND,
                EventName.UPDATE_CHECK_FINISHED,
                EventName.UPDATE_CHECK_STARTED,
                EventName.UPDATED_URL_FOUND,
                EventName.UPDATED_URL_FOUND,
                EventName.UPDATE_CHECK_FINISHED,
            ]
        finally:
            await service.close()

    asyncio.run(scenario())


def test_concurrent_auth_refresh_applies_credentials_once_per_retry() -> None:
    class Flow:
        allowed_origins: tuple[str, ...] = ()

        def __init__(self) -> None:
            self.token = 0
            self.apply_calls = 0
            self.refresh_calls = 0

        async def apply(self, request: RequestSpec) -> RequestSpec:
            self.apply_calls += 1
            return replace(request, headers={**request.headers, "Authorization": f"Bearer {self.token}"})

        async def refresh(self, request: RequestSpec, response: RequestResponse) -> RequestSpec:
            self.refresh_calls += 1
            self.token += 1
            return request

        def is_auth_failure(self, request: RequestSpec, response: RequestResponse) -> bool:
            return False

    async def scenario() -> None:
        gateway = RequestGateway(AppConfig())
        flow = Flow()
        session = gateway.operation(
            plugin_id="com.example.gallery",
            operation_url="https://example.test/gallery",
            auth_flow_factory=lambda _: flow,
        )
        initial_requests = 0

        async def transport(
            request: RequestSpec,
            *,
            plugin_id: str | None = None,
            allowed_redirect_origins: frozenset[str] | None = None,
        ) -> RequestResponse:
            assert plugin_id == "com.example.gallery"
            assert allowed_redirect_origins == frozenset({"https://example.test:443"})
            nonlocal initial_requests
            if request.headers.get("Authorization") == "Bearer 0":
                initial_requests += 1
                while initial_requests < 2:
                    await asyncio.sleep(0)
                return RequestResponse(request.url, 401, {}, b"")
            return RequestResponse(request.url, 200, {}, b"ok")

        gateway._transport = transport  # type: ignore[method-assign]
        try:
            await asyncio.gather(
                session.execute(RequestSpec("https://example.test/a")),
                session.execute(RequestSpec("https://example.test/b")),
            )
            assert flow.refresh_calls == 1
            assert flow.apply_calls == 4
        finally:
            await gateway.close()

    asyncio.run(scenario())


def test_concurrent_operation_sessions_do_not_share_auth_or_plugin_identity() -> None:
    class Flow:
        def __init__(self, token: str) -> None:
            self.token = token

        async def apply(self, request: RequestSpec) -> RequestSpec:
            return replace(request, headers={**request.headers, "Authorization": self.token})

        async def refresh(self, request: RequestSpec, response: RequestResponse) -> RequestSpec | None:
            return None

        def is_auth_failure(self, request: RequestSpec, response: RequestResponse) -> bool:
            return False

    async def scenario() -> None:
        gateway = RequestGateway(AppConfig())
        observed: list[tuple[str | None, str | None]] = []

        async def transport(
            request: RequestSpec,
            *,
            plugin_id: str | None = None,
            allowed_redirect_origins: frozenset[str] | None = None,
        ) -> RequestResponse:
            assert allowed_redirect_origins == frozenset({"https://example.test:443"})
            await asyncio.sleep(0)
            token = request.headers.get("Authorization")
            observed.append((plugin_id, token))
            return RequestResponse(request.url, 200, {}, (token or "").encode())

        gateway._transport = transport  # type: ignore[method-assign]
        first = gateway.operation(
            plugin_id="com.example.first",
            operation_url="https://example.test/first",
            auth_flow_factory=lambda _: Flow("first-token"),
        )
        second = gateway.operation(
            plugin_id="com.example.second",
            operation_url="https://example.test/second",
            auth_flow_factory=lambda _: Flow("second-token"),
        )
        try:
            responses = await asyncio.gather(
                first.execute(RequestSpec("https://example.test/a")),
                second.execute(RequestSpec("https://example.test/b")),
            )
            assert [response.body for response in responses] == [b"first-token", b"second-token"]
            assert set(observed) == {
                ("com.example.first", "first-token"),
                ("com.example.second", "second-token"),
            }
        finally:
            await gateway.close()

    asyncio.run(scenario())


def test_download_service_creates_a_distinct_request_session_for_each_operation(tmp_path: Path) -> None:
    async def scenario() -> None:
        data_root = (tmp_path / "data").resolve()
        plugin_root = (tmp_path / "plugins").resolve()
        config = AppConfig.model_validate(
            {
                "storage": {"data_root": str(data_root)},
                "plugins": {"root": str(plugin_root)},
                "security": {"plugin_verification": "off"},
            }
        )
        service = RuntimeComposer(config, config_root=tmp_path.resolve(), plugin_root=plugin_root).compose()
        try:
            first = service._operation("https://example.test/first")
            second = service._operation("https://example.test/second")
            assert first[3] is not second[3]
            assert first[2].requests is not second[2].requests
            assert first[3]._shared is service.gateway
            assert second[3]._shared is service.gateway
        finally:
            await service.close()

    asyncio.run(scenario())


def test_close_releases_gateway_and_logger_when_cookie_delta_persistence_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        data_root = (tmp_path / "data").resolve()
        plugin_root = (tmp_path / "plugins").resolve()
        config = AppConfig.model_validate(
            {
                "storage": {"data_root": str(data_root)},
                "plugins": {"root": str(plugin_root)},
                "security": {"plugin_verification": "off"},
            }
        )
        service = RuntimeComposer(config, config_root=tmp_path.resolve(), plugin_root=plugin_root).compose()
        service.gateway.client.cookies.set("session", "secret", domain="example.test")
        closed: list[str] = []

        def fail_save(_baseline: object, _jar: object) -> None:
            raise AuthenticationError("save failed")

        async def close_gateway() -> None:
            closed.append("gateway")
            await service.gateway.client.aclose()

        async def close_logger() -> None:
            closed.append("logger")

        def close_image_processor() -> None:
            closed.append("image")

        monkeypatch.setattr(service.cookie_store, "persist_delta", fail_save)
        monkeypatch.setattr(service.gateway, "close", close_gateway)
        monkeypatch.setattr(service.image_processor, "close", close_image_processor)
        monkeypatch.setattr(service.logger, "close", close_logger)
        with pytest.raises(AuthenticationError, match="save failed"):
            await service.close()
        assert closed == ["gateway", "image", "logger"]

    asyncio.run(scenario())
