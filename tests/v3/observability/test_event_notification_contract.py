from __future__ import annotations

import asyncio
import io
from pathlib import Path

import httpx
import pytest
from PIL import Image

from image_downloader.config import AppConfig, Notification
from image_downloader.exceptions import (
    AuthenticationError,
    ConfigurationError,
    ExistingFileConflictError,
    ImageDownloaderError,
    StorageError,
)
from image_downloader.observability.events import EventBus, EventName, EventPayload
from image_downloader.observability.logging import DownloadLogger
from image_downloader.observability.notifications import NotificationService, validate_notification_delivery
from image_downloader.runtime import ArtifactPipeline, DownloadService, RuntimeComposer
from image_downloader.storage import FileSystem
from image_downloader.transport.gateway import OperationRequestGateway


def _png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(output, format="PNG")
    return output.getvalue()


def _service(
    tmp_path: Path,
    *,
    continue_on_image_error: bool = True,
    existing_file: str = "skip",
) -> DownloadService:
    plugin_root = (tmp_path / "plugins").resolve()
    config = AppConfig.model_validate(
        {
            "storage": {"data_root": str((tmp_path / "data").resolve())},
            "plugins": {"root": str(plugin_root)},
            "security": {"plugin_verification": "off"},
            "output": {"existing_file": existing_file},
            "logging": {"console": {"enabled": False}},
            "network": {"max_attempts": 1},
            "download": {"continue_on_image_error": continue_on_image_error},
        }
    )
    return RuntimeComposer(config, config_root=tmp_path.resolve(), plugin_root=plugin_root).compose()


async def _mock_gallery(service: DownloadService, paths: tuple[str, ...], *, failing: str | None = None) -> None:
    await service.gateway.client.aclose()
    html = "<title>Gallery</title>" + "".join(f"<img src='/{path}'>" for path in paths)
    data = _png()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/gallery":
            return httpx.Response(200, headers={"content-type": "text/html"}, text=html, request=request)
        if request.url.path == f"/{failing}":
            return httpx.Response(404, request=request)
        return httpx.Response(200, headers={"content-type": "image/png"}, content=data, request=request)

    service.gateway.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _record_events(service: DownloadService) -> list[EventName]:
    observed: list[EventName] = []
    for event in EventName:
        service.events.on(event, lambda _payload, event=event: observed.append(event))
    return observed


def test_success_emits_image_and_operation_events_in_order(tmp_path: Path) -> None:
    async def scenario() -> list[EventName]:
        service = _service(tmp_path)
        await _mock_gallery(service, ("good.png",))
        observed = _record_events(service)
        try:
            result = await service.run("https://example.test/gallery")
            assert len(result.saved_files) == 1
            return observed
        finally:
            await service.close()

    assert asyncio.run(scenario()) == [
        EventName.BEFORE_DOWNLOAD,
        EventName.BEFORE_FETCH,
        EventName.FETCH_SUCCESS,
        EventName.IMAGE_PROCESS_STARTED,
        EventName.IMAGE_PROCESS_SUCCESS,
        EventName.BEFORE_SAVE,
        EventName.SAVE_SUCCESS,
        EventName.DOWNLOAD_SUCCESS,
        EventName.DOWNLOAD_COMPLETE,
        EventName.AFTER_DOWNLOAD,
    ]


@pytest.mark.parametrize(
    ("stage", "failure_event"),
    [
        ("fetch", EventName.FETCH_FAILED),
        ("process", EventName.IMAGE_PROCESS_FAILED),
        ("save", EventName.SAVE_FAILED),
    ],
)
@pytest.mark.parametrize("continue_on_image_error", [True, False])
def test_stage_failure_emitted_once_even_in_fail_fast(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    failure_event: EventName,
    continue_on_image_error: bool,
) -> None:
    if stage == "process":

        async def fail_process(self, *args, **kwargs):
            raise RuntimeError("processing failed")

        monkeypatch.setattr(ArtifactPipeline, "process", fail_process)
    elif stage == "save":
        original_write = FileSystem.write_bytes_atomic

        def fail_save(self, path, data):
            if str(path).endswith(".jpeg"):
                raise OSError("save failed")
            return original_write(self, path, data)

        monkeypatch.setattr(FileSystem, "write_bytes_atomic", fail_save)

    async def scenario() -> list[EventName]:
        service = _service(tmp_path, continue_on_image_error=continue_on_image_error)
        await _mock_gallery(service, ("bad.png",), failing="bad.png" if stage == "fetch" else None)
        observed = _record_events(service)
        try:
            if continue_on_image_error:
                result = await service.run("https://example.test/gallery")
                assert len(result.failures) == 1
                if stage == "save":
                    assert result.failures[0].exception_type == "StorageError"
                    assert result.failures[0].code == "storage_error"
            else:
                expected_error = StorageError if stage == "save" else ImageDownloaderError
                with pytest.raises(expected_error):
                    await service.run("https://example.test/gallery")
            return observed
        finally:
            await service.close()

    observed = asyncio.run(scenario())
    assert observed.count(failure_event) == 1
    assert EventName.DOWNLOAD_FAILED in observed
    assert EventName.DOWNLOAD_PARTIAL_SUCCESS not in observed
    if stage == "process":
        assert EventName.SAVE_FAILED not in observed
    assert observed.index(failure_event) < observed.index(EventName.DOWNLOAD_FAILED)
    assert observed[-1] is EventName.AFTER_DOWNLOAD
    assert (EventName.DOWNLOAD_COMPLETE in observed) is continue_on_image_error


def test_mixed_results_emit_partial_success(tmp_path: Path) -> None:
    async def scenario() -> tuple[list[EventName], int, int]:
        service = _service(tmp_path)
        await _mock_gallery(service, ("good.png", "bad.png"), failing="bad.png")
        observed = _record_events(service)
        try:
            first = await service.run("https://example.test/gallery")
            second = await service.run("https://example.test/gallery")
            return observed, len(first.saved_files), len(second.skipped_files)
        finally:
            await service.close()

    observed, saved, skipped = asyncio.run(scenario())
    assert saved == 1
    assert skipped == 1
    assert observed.count(EventName.FETCH_FAILED) == 2
    assert observed.count(EventName.SAVE_SUCCESS) == 1
    assert observed.count(EventName.DOWNLOAD_PARTIAL_SUCCESS) == 2
    assert EventName.DOWNLOAD_FAILED not in observed


def test_image_decode_failure_has_transport_context_in_events_notifications_and_chapter_log(tmp_path: Path) -> None:
    class CapturingSender:
        def __init__(self) -> None:
            self.messages: list[str] = []

        async def send(self, _title: str, message: str) -> bool:
            self.messages.append(message)
            return True

    async def scenario() -> tuple[EventPayload, str, str, object]:
        service = _service(tmp_path)
        await service.gateway.client.aclose()

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/gallery":
                return httpx.Response(200, text="<img src='/bad.png'>", request=request)
            return httpx.Response(
                200,
                headers={"content-type": "image/png"},
                content=b"not an image",
                request=request,
            )

        service.gateway.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        payloads: list[EventPayload] = []
        service.events.on(EventName.IMAGE_PROCESS_FAILED, payloads.append)
        sender = CapturingSender()
        notifications = NotificationService(
            Notification(enabled=True, methods=("desktop",), notify_on=("process_error",)),
            service.logger,
            service.events,
            {"desktop": sender},
        )
        try:
            result = await service.run("https://example.test/gallery")
            await notifications.flush(source_url="https://example.test/gallery")
            log = next((tmp_path / "data").rglob("log.log")).read_text(encoding="utf-8")
            return payloads[0], sender.messages[0], log, result.failures[0]
        finally:
            await service.close()

    payload, message, log, failure = asyncio.run(scenario())
    assert payload.stage == "image_processing"
    assert payload.http_status == 200
    assert payload.error_code == "image_decode_error"
    assert payload.error_reason == "image data cannot be decoded"
    assert payload.error_class == "ImageDecodeError"
    assert "image_processing_failed: count=1" in message
    assert "transport: completed" in message
    assert "reason_code: image_decode_error" in message
    assert "exception: ImageDecodeError" in message
    assert "error: image_processing_failed (count=1)" in log
    assert "image_url: https://example.test/bad.png" in log
    assert "http_status: 200" in log
    assert "transport: completed" in log
    assert "exception: ImageDecodeError" in log
    assert failure.code == "image_decode_error"
    assert failure.http_status == 200


def test_http_status_failure_keeps_final_response_url_and_transport_state(tmp_path: Path) -> None:
    async def scenario() -> tuple[EventPayload, str, object]:
        service = _service(tmp_path)
        await service.gateway.client.aclose()

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/gallery":
                return httpx.Response(200, text="<img src='/bad.png'>", request=request)
            if request.url.host == "example.test":
                return httpx.Response(302, headers={"Location": "https://cdn.test/final.png"}, request=request)
            return httpx.Response(404, request=request)

        service.gateway.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        payloads: list[EventPayload] = []
        service.events.on(EventName.FETCH_FAILED, payloads.append)
        try:
            result = await service.run("https://example.test/gallery")
            log = next((tmp_path / "data").rglob("log.log")).read_text(encoding="utf-8")
            return payloads[0], log, result.failures[0]
        finally:
            await service.close()

    payload, log, failure = asyncio.run(scenario())
    assert payload.response_url == "https://cdn.test/final.png"
    assert payload.http_status == 404
    assert payload.transport == "response_received"
    assert payload.error_code == "http_status_error"
    assert "response_url: https://cdn.test/final.png" in log
    assert "transport: response_received" in log
    assert failure.response_url == "https://cdn.test/final.png"


def test_existing_file_error_is_a_controlled_save_conflict_with_diagnostics(tmp_path: Path) -> None:
    class CapturingSender:
        def __init__(self) -> None:
            self.messages: list[str] = []

        async def send(self, _title: str, message: str) -> bool:
            self.messages.append(message)
            return True

    async def scenario() -> tuple[EventPayload, str, str, object, object]:
        target = tmp_path / "data" / "profiles" / "default" / "downloads" / "0001_Gallery" / "0001.jpeg"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"already saved")
        service = _service(tmp_path, existing_file="error")
        await _mock_gallery(service, ("blocked.png", "saved.png"))
        payloads: list[EventPayload] = []
        service.events.on(EventName.SAVE_FAILED, payloads.append)
        sender = CapturingSender()
        notifications = NotificationService(
            Notification(enabled=True, methods=("desktop",), notify_on=("save_error",)),
            service.logger,
            service.events,
            {"desktop": sender},
        )
        try:
            result = await service.run("https://example.test/gallery")
            await notifications.flush(source_url="https://example.test/gallery")
            log = next((tmp_path / "data").rglob("log.log")).read_text(encoding="utf-8")
            return payloads[0], sender.messages[0], log, result, result.failures[0]
        finally:
            await service.close()

    payload, message, log, result, failure = asyncio.run(scenario())
    assert len(result.saved_files) == 1
    assert len(result.failures) == 1
    assert payload.stage == "image_save"
    assert payload.http_status == 200
    assert payload.path is not None and payload.path.endswith("0001_Gallery/0001.jpeg")
    assert payload.error_code == "existing_file_conflict"
    assert payload.error_reason == "output file already exists and existing-file=error prevents overwrite"
    assert payload.error_class == "ExistingFileConflictError"
    assert failure.exception_type == "ExistingFileConflictError"
    assert failure.code == "existing_file_conflict"
    assert failure.output_path is not None
    assert Path(failure.output_path).as_posix().endswith("0001_Gallery/0001.jpeg")
    assert "image_save_failed: count=1" in message
    assert "reason_code: existing_file_conflict" in message
    assert "output_path: 0001_Gallery/0001.jpeg" in message
    assert "transport: completed" in message
    assert "exception: ExistingFileConflictError" in message
    assert "StorageError" not in message
    assert "error: image_save_failed (count=1)" in log
    assert "save: 0001_Gallery/0001.jpeg" in log
    assert "exception: ExistingFileConflictError" in log
    assert "unexpected image save failure" not in log


def test_existing_file_error_raises_the_specific_exception_when_fail_fast(tmp_path: Path) -> None:
    async def scenario() -> None:
        target = tmp_path / "data" / "profiles" / "default" / "downloads" / "0001_Gallery" / "0001.jpeg"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"already saved")
        service = _service(tmp_path, continue_on_image_error=False, existing_file="error")
        await _mock_gallery(service, ("blocked.png",))
        try:
            with pytest.raises(ExistingFileConflictError) as raised:
                await service.run("https://example.test/gallery")
            assert raised.value.relative_path == Path("0001_Gallery") / "0001.jpeg"
            assert raised.value.policy == "error"
        finally:
            await service.close()

    asyncio.run(scenario())


def test_fail_fast_save_error_keeps_stage_context_in_event_and_chapter_log(tmp_path: Path) -> None:
    async def scenario() -> tuple[EventPayload, str]:
        target = tmp_path / "data" / "profiles" / "default" / "downloads" / "0001_Gallery" / "0001.jpeg"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"already saved")
        service = _service(tmp_path, continue_on_image_error=False, existing_file="error")
        await _mock_gallery(service, ("blocked.png",))
        payloads: list[EventPayload] = []
        service.events.on(EventName.SAVE_FAILED, payloads.append)
        try:
            with pytest.raises(ExistingFileConflictError):
                await service.run("https://example.test/gallery")
            log = next((tmp_path / "data").rglob("log.log")).read_text(encoding="utf-8")
            return payloads[0], log
        finally:
            await service.close()

    payload, log = asyncio.run(scenario())
    assert payload.stage == "image_save"
    assert payload.transport == "completed"
    assert payload.error_code == "existing_file_conflict"
    assert "error: image_save_failed (count=1)" in log
    assert "transport: completed" in log
    assert "exception: ExistingFileConflictError" in log


def test_image_authentication_failure_uses_fetch_category_with_image_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_image(*_args: object, **_kwargs: object) -> object:
        raise AuthenticationError("token=secret")

    monkeypatch.setattr(OperationRequestGateway, "execute_image", fail_image)

    async def scenario() -> tuple[list[EventName], EventPayload, str]:
        service = _service(tmp_path)
        await _mock_gallery(service, ("protected.png",))
        observed = _record_events(service)
        payloads: list[EventPayload] = []
        service.events.on(EventName.FETCH_FAILED, payloads.append)
        try:
            with pytest.raises(AuthenticationError):
                await service.run("https://example.test/gallery")
            log = next((tmp_path / "data").rglob("log.log")).read_text(encoding="utf-8")
            return observed, payloads[0], log
        finally:
            await service.close()

    observed, payload, log = asyncio.run(scenario())
    assert observed.count(EventName.FETCH_FAILED) == 1
    assert EventName.AUTH_FAILED not in observed
    assert payload.stage == "image_fetch"
    assert payload.error_code == "authentication_error"
    assert payload.error_class == "AuthenticationError"
    assert "reason_code: authentication_error" in log


def test_cancelled_operation_emits_after_but_not_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> list[EventName]:
        service = _service(tmp_path)
        observed = _record_events(service)
        started = asyncio.Event()

        async def blocked(*_args, **_kwargs):
            started.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(service, "_run_operation", blocked)
        try:
            task = asyncio.create_task(service.run("https://example.test/gallery"))
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            return observed
        finally:
            await service.close()

    assert asyncio.run(scenario()) == [EventName.BEFORE_DOWNLOAD, EventName.AFTER_DOWNLOAD]


def test_notification_defaults_off_and_validates_only_active_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    assert not Notification().enabled
    monkeypatch.setattr("image_downloader.observability.notifications.importlib.util.find_spec", lambda _name: None)
    validate_notification_delivery(Notification())
    with pytest.raises(ConfigurationError, match=r"image-downloader\[notify\]"):
        validate_notification_delivery(Notification(enabled=True))
    email = {"smtp_host": "smtp.example.test", "from": "sender@example.test", "to": ["to@example.test"]}
    validate_notification_delivery(
        Notification.model_validate(
            {
                "enabled": True,
                "methods": ["desktop"],
                "notify_on": ["fetch_error"],
                "routes": {"fetch_error": ["email"]},
                "email": email,
            }
        )
    )
    with pytest.raises(ConfigurationError, match="smtp_host"):
        validate_notification_delivery(Notification(enabled=True, methods=("email",)))
    with pytest.raises(ConfigurationError, match="at least one delivery method"):
        validate_notification_delivery(Notification(enabled=True, methods=()))


def test_compose_rejects_missing_desktop_dependency_before_building_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("image_downloader.observability.notifications.importlib.util.find_spec", lambda _name: None)
    plugin_root = (tmp_path / "plugins").resolve()
    config = AppConfig.model_validate(
        {
            "storage": {"data_root": str((tmp_path / "data").resolve())},
            "plugins": {"root": str(plugin_root)},
            "notification": {"enabled": True},
        }
    )
    with pytest.raises(ConfigurationError, match=r"image-downloader\[notify\]"):
        RuntimeComposer(config, config_root=tmp_path.resolve(), plugin_root=plugin_root).compose()


def test_process_error_route_and_false_sender_result_are_reported() -> None:
    class DecliningSender:
        def __init__(self) -> None:
            self.messages: list[str] = []

        async def send(self, _title: str, _message: str) -> bool:
            self.messages.append(_message)
            return False

    async def scenario() -> tuple[list[EventName], list[str]]:
        logger = DownloadLogger()
        events = EventBus(logger)
        observed: list[EventName] = []
        events.on(EventName.NOTIFICATION_FAILED, lambda _payload: observed.append(EventName.NOTIFICATION_FAILED))
        sender = DecliningSender()
        service = NotificationService(
            Notification(enabled=True, notify_on=("process_error",), methods=("desktop",)),
            logger,
            events,
            {"desktop": sender},
        )
        await events.emit(EventName.IMAGE_PROCESS_FAILED, EventPayload(url="https://example.test"))
        await service.flush(source_url="https://example.test")
        await logger.close()
        return observed, sender.messages

    observed, messages = asyncio.run(scenario())
    assert observed == [EventName.NOTIFICATION_FAILED]
    assert "image_processing_failed: count=1" in messages[0]
    assert "save_error" not in messages[0]
