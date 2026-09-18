from __future__ import annotations

import asyncio
import io
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

import image_downloader.cli as cli
import image_downloader.commands.download as cli_download
import image_downloader.observability.diagnostic_safety as diagnostic_safety
from image_downloader.config import AppConfig
from image_downloader.observability.events import EventName
from image_downloader.runtime import DownloadService, RuntimeComposer

SECRET = "https://private.test/path?token=secret Cookie: session=secret"


def _service(tmp_path: Path) -> DownloadService:
    plugin_root = (tmp_path / "plugins").resolve()
    config = AppConfig.model_validate(
        {
            "storage": {"data_root": str((tmp_path / "data").resolve())},
            "plugins": {"root": str(plugin_root)},
            "security": {"plugin_verification": "off"},
            "logging": {"console": {"enabled": False}},
        }
    )
    return RuntimeComposer(config, config_root=tmp_path.resolve(), plugin_root=plugin_root).compose()


async def _gallery(service: DownloadService) -> None:
    await service.gateway.client.aclose()
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(buffer, format="PNG")
    image = buffer.getvalue()

    def response(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/gallery":
            return httpx.Response(200, text="<img src='/one.png'>", request=request)
        return httpx.Response(200, headers={"content-type": "image/png"}, content=image, request=request)

    service.gateway.client = httpx.AsyncClient(transport=httpx.MockTransport(response))


def _inject_core_failure(service: DownloadService, monkeypatch: pytest.MonkeyPatch, stage: str) -> None:
    original = service.logger.core

    async def broken_core(event: str, **kwargs: object) -> None:
        target = event == stage and (stage != "download_finished" or kwargs.get("action") is None)
        if target:
            raise OSError(SECRET)
        await original(event, **kwargs)

    monkeypatch.setattr(service.logger, "core", broken_core)


def _inject_logger_failure(service: DownloadService, monkeypatch: pytest.MonkeyPatch, stage: str) -> None:
    if stage in {"file_saved", "chapter_finished", "download_finished"}:
        _inject_core_failure(service, monkeypatch, stage)
    elif stage == "sink_write":

        async def broken_sink(_record: object) -> None:
            raise OSError(SECRET)

        monkeypatch.setattr(service.logger.sinks[0], "write", broken_sink)
    elif stage == "sink_close":

        async def broken_sink_close() -> None:
            raise OSError(SECRET)

        monkeypatch.setattr(service.logger.sinks[0], "close", broken_sink_close)
    elif stage == "register_chapter":

        def broken_register(*_args: object, **_kwargs: object) -> None:
            raise OSError(SECRET)

        monkeypatch.setattr(service.logger, "register_chapter", broken_register)
    elif stage in {"chapter_save", "chapter_done", "chapter_header"}:

        async def broken_chapter(*_args: object, **_kwargs: object) -> None:
            raise OSError(SECRET)

        monkeypatch.setattr(service.logger, stage, broken_chapter)
    elif stage in {"close_chapter", "close", "flush_python_log_capture"}:
        original_close = getattr(service.logger, stage)

        async def broken_close(*args: object, **kwargs: object) -> None:
            await original_close(*args, **kwargs)
            raise OSError(SECRET)

        monkeypatch.setattr(service.logger, stage, broken_close)
    elif stage == "notification_flush":

        async def broken_notification(*_args: object, **_kwargs: object) -> None:
            raise OSError(SECRET)

        monkeypatch.setattr(service.notifications, "flush", broken_notification)
    else:
        raise AssertionError(stage)


@pytest.mark.parametrize(
    "stage",
    (
        "sink_write",
        "sink_close",
        "register_chapter",
        "chapter_header",
        "file_saved",
        "chapter_save",
        "chapter_done",
        "chapter_finished",
        "download_finished",
        "close_chapter",
        "flush_python_log_capture",
        "notification_flush",
        "close",
    ),
)
def test_diagnostic_failure_keeps_saved_result_and_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], stage: str
) -> None:
    async def scenario() -> None:
        service = _service(tmp_path)
        await _gallery(service)
        observed: list[EventName] = []
        for event in (EventName.SAVE_SUCCESS, EventName.SAVE_FAILED, EventName.DOWNLOAD_SUCCESS):
            service.events.on(event, lambda _payload, event=event: observed.append(event))
        _inject_logger_failure(service, monkeypatch, stage)
        try:
            result = await service.run("https://example.test/gallery")
            assert len(result.saved_files) == 1
            assert not result.failures
            assert Path(result.saved_files[0]).is_file()
            assert observed == [EventName.SAVE_SUCCESS, EventName.DOWNLOAD_SUCCESS]
        finally:
            await service.close()

    asyncio.run(scenario())
    warning = capsys.readouterr().err
    assert "warning: diagnostic logging failed" in warning
    assert SECRET not in warning
    assert "token=secret" not in warning
    assert "session=secret" not in warning


def test_stderr_failure_cannot_change_saved_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenStderr:
        def write(self, _value: str) -> None:
            raise OSError(SECRET)

    async def scenario() -> None:
        service = _service(tmp_path)
        await _gallery(service)
        _inject_logger_failure(service, monkeypatch, "file_saved")
        monkeypatch.setattr(diagnostic_safety, "sys", SimpleNamespace(stderr=BrokenStderr()))
        try:
            result = await service.run("https://example.test/gallery")
            assert len(result.saved_files) == 1
        finally:
            await service.close()

    asyncio.run(scenario())


def test_cli_success_exit_code_survives_post_save_log_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        service = _service(tmp_path)
        await _gallery(service)
        _inject_logger_failure(service, monkeypatch, "file_saved")
        config = service.config

        class Composer:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def compose(self) -> DownloadService:
                return service

        monkeypatch.setattr(cli_download, "RuntimeComposer", Composer)
        monkeypatch.setattr(
            cli_download,
            "_config_for",
            lambda *_args, **_kwargs: (config, tmp_path.resolve(), tmp_path.resolve(), "test"),
        )
        args = cli.build_parser().parse_args(["download", "https://example.test/gallery"])
        assert await cli.run(args) == cli.EXIT_SUCCESS

    asyncio.run(scenario())
