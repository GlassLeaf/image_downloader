from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from image_downloader.application.log_verification import stable_file, verify_logs
from image_downloader.exceptions import StorageSafetyError
from image_downloader.observability.logging import ChapterFailureRecord, DebugFileSink, DownloadLogger
from image_downloader.storage.filesystem import FileSystem

HEADER = "---\nurl: https://example.test/gallery\ntitle: error in a title\n---\n"


def test_all_history_preserves_errors_without_keyword_false_positives(tmp_path):
    path = tmp_path / "log.log"
    path.write_text(
        HEADER + "download: https://example.test/error\n"
        "error: image_fetch_failed (count=2)\nreason: error detail\ndone\n" + HEADER + "save: error.png\ndone\n",
        encoding="utf-8-sig",
    )
    before = path.read_bytes()
    result = verify_logs(tmp_path)
    assert result["status"] == "failed"
    assert result["summary"] == {"files": 1, "errors": 1, "indeterminate": 0}
    assert result["files"][0]["errors"][0]["line"] == 6
    assert path.read_bytes() == before


@pytest.mark.parametrize("stage", ["image_fetch", "image_processing", "image_save"])
def test_actual_logger_chapter_failure_is_detected(tmp_path, stage):
    async def scenario():
        logger = DownloadLogger()
        logger.register_chapter("1", tmp_path / "log.log")
        await logger.chapter_header("1", url="https://example.test/error", title="error", subtitle=None)
        await logger.chapter_error_group(
            "1",
            stage + "_failed",
            [
                ChapterFailureRecord(
                    "https://example.test/image",
                    None,
                    None,
                    stage,
                    1,
                    404,
                    "http_status_error",
                    "failed",
                    "HttpStatusError",
                    "token=secret",
                )
            ],
        )
        await logger.chapter_done("1")
        await logger.close()

    asyncio.run(scenario())
    result = verify_logs(tmp_path)
    assert result["status"] == "failed"
    assert result["summary"]["errors"] == 1
    assert "secret" not in str(result)


def test_actual_debug_log_errors_warning_and_retry(tmp_path):
    async def scenario():
        logger = DownloadLogger([DebugFileSink(tmp_path / "debug.log")])
        await logger.core("request_retry", module="http", debug=True)
        await logger.core("download_failed", module="download", error=ValueError("error"), debug=True)
        await logger.error_detail(ValueError("token=secret"))
        await logger.debug("event=python_log level=WARNING logger=test message=error")
        await logger.debug("event=python_log level=ERROR logger=test message=token=secret")
        await logger.close()

    asyncio.run(scenario())
    result = verify_logs(tmp_path / "debug.log")
    assert result["status"] == "failed"
    assert result["summary"]["errors"] == 3
    assert "secret" not in str(result)
    assert verify_logs(tmp_path)["status"] == "indeterminate"  # Directory scans only chapter logs.


@pytest.mark.parametrize(
    "content",
    [b"", b"garbage", (HEADER + "download: image\n").encode(), (HEADER + "done\n" + HEADER).encode(), b"\xff"],
)
def test_incomplete_invalid_and_empty_logs_are_indeterminate(tmp_path, content):
    (tmp_path / "log.log").write_bytes(content)
    assert verify_logs(tmp_path)["status"] == "indeterminate"


def test_discovery_order_missing_and_unreadable(tmp_path, monkeypatch):
    assert verify_logs(tmp_path)["summary"]["indeterminate"] == 1
    for name in ("z", "a"):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "log.log").write_text(HEADER + "done\n", encoding="utf-8")
    assert [file["path"] for file in verify_logs(tmp_path)["files"]] == ["a/log.log", "z/log.log"]
    original = os.open

    def denied(path, *args, **kwargs):
        if Path(path).parent.name == "a":
            raise PermissionError
        return original(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", denied)
    result = verify_logs(tmp_path)
    assert result["status"] == "indeterminate"
    assert len(result["files"]) == 2


def test_file_modified_during_verification_is_rejected(tmp_path):
    path = tmp_path / "log.log"
    path.write_text(HEADER + "done\n", encoding="utf-8")
    with pytest.raises(StorageSafetyError):
        with stable_file(FileSystem(tmp_path), Path("log.log")) as stream:
            stream.read()
            with path.open("ab") as writer:
                writer.write(b"changed\n")


def test_symlink_is_not_followed(tmp_path):
    target = tmp_path / "outside"
    target.mkdir()
    (target / "log.log").write_text(HEADER + "done\n", encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    try:
        (root / "linked").symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    result = verify_logs(root)
    assert result["status"] == "indeterminate"
    assert result["summary"]["files"] == 0
