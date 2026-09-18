from __future__ import annotations

import asyncio
import io
from pathlib import Path

import httpx
import pytest
from PIL import Image

from image_downloader.config import AppConfig, Notification
from image_downloader.exceptions import ConfigurationError, DownloaderError
from image_downloader.observability.events import EventBus, EventName, EventPayload
from image_downloader.observability.logging import DownloadLogger
from image_downloader.observability.notifications import NotificationService, validate_notification_delivery
from image_downloader.runtime import ArtifactPipeline, DownloadService, RuntimeComposer
from image_downloader.storage import FileSystem


def _png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(output, format="PNG")
    return output.getvalue()


def _service(tmp_path: Path, *, continue_on_error: bool = True) -> DownloadService:
    plugin_root = (tmp_path / "plugins").resolve()
    config = AppConfig.model_validate(
        {
            "storage": {"data_root": str((tmp_path / "data").resolve())},
            "plugins": {"root": str(plugin_root)},
            "security": {"plugin_verification": "off"},
            "output": {"existing_file": "skip"},
            "logging": {"console": {"enabled": False}},
            "network": {"max_retries": 1},
            "continue_on_error": continue_on_error,
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
@pytest.mark.parametrize("continue_on_error", [True, False])
def test_stage_failure_emitted_once_even_in_fail_fast(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    failure_event: EventName,
    continue_on_error: bool,
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
        service = _service(tmp_path, continue_on_error=continue_on_error)
        await _mock_gallery(service, ("bad.png",), failing="bad.png" if stage == "fetch" else None)
        observed = _record_events(service)
        try:
            if continue_on_error:
                result = await service.run("https://example.test/gallery")
                assert len(result.failures) == 1
            else:
                with pytest.raises(DownloaderError):
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
    assert (EventName.DOWNLOAD_COMPLETE in observed) is continue_on_error


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
    assert "process_error: 1" in messages[0]
    assert "save_error" not in messages[0]
