from __future__ import annotations

import asyncio
import io
from pathlib import Path

from image_downloader.observability.logging import (
    ChapterFileSink,
    ConsoleSink,
    DebugFileSink,
    DownloadLogger,
    mask_log_text,
    mask_sensitive,
    safe_url,
)


def test_file_and_console_receive_identical_masked_content(tmp_path: Path) -> None:
    async def scenario() -> tuple[str, str]:
        output = io.StringIO()
        file_sink = ChapterFileSink(tmp_path / "log.log")
        logger = DownloadLogger([file_sink, ConsoleSink(output)])
        await logger.log("download: https://example.test/image?token=secret&n=1")
        await logger.log("save: output/0001.jpeg")
        await logger.close()
        return (tmp_path / "log.log").read_text(encoding="utf-8"), output.getvalue()

    file_content, console_content = asyncio.run(scenario())
    assert file_content == console_content
    assert "secret" not in file_content
    assert "[REDACTED]" in file_content


def test_sensitive_headers_are_masked() -> None:
    value = "Authorization: Bearer abc123 Cookie: sid=private csrf=csrf-value https://user:pass@example.test/?api_key=api-value"
    masked = mask_sensitive(value)
    assert "abc123" not in masked
    assert "private" not in masked
    assert "csrf-value" not in masked
    assert "pass" not in masked
    assert "api-value" not in masked


def test_mask_log_text_masks_json_forms_cookies_and_urls_without_losing_normal_text() -> None:
    value = (
        'HTTP 401: title=Visible, {"refresh_token": "refresh-value", "private_key": "key-value"}; '
        "Set-Cookie: sid=cookie-value; Path=/; HttpOnly\n"
        "form session=example-session&csrf=csrf-value Bearer bearer-value "
        "https://user:password@example.test/a?sig=signed-value&page=2#format=webp"
    )

    masked = mask_log_text(value)

    for secret in (
        "refresh-value",
        "key-value",
        "cookie-value",
        "example-session",
        "csrf-value",
        "bearer-value",
        "password",
        "signed-value",
    ):
        assert secret not in masked
    assert "HTTP 401" in masked
    assert "title=Visible" in masked
    assert "Set-Cookie: [REDACTED]" in masked
    assert "page=2" in masked
    assert "format=webp" in masked


def test_safe_url_uses_allowlists_and_redacts_userinfo_fragment_and_unknown_values() -> None:
    value = (
        "https://user:password@example.test/gallery?"
        "page=2&api_key=LEAK&custom=visible#view=grid&access_token=LEAK&opaque"
    )
    rendered = safe_url(value, safe_query_parameters={"custom"}, safe_fragment_parameters={"view"})
    assert "password" not in rendered
    assert "LEAK" not in rendered
    assert "userinfo=[REDACTED]" in rendered
    assert "page=2" in rendered and "custom=visible" in rendered and "view=grid" in rendered
    assert "api_key=[REDACTED]" in rendered and "access_token=[REDACTED]" in rendered
    assert "opaque=[REDACTED]" in rendered


def test_core_logger_only_renders_allowed_fields(tmp_path: Path) -> None:
    async def scenario() -> str:
        output = io.StringIO()
        logger = DownloadLogger([ConsoleSink(output)])
        logger.configure_safety({"safe_query_parameters": ["edition"]}, tmp_path / "downloads")
        await logger.core(
            "request_started", module="http", method="GET",
            url="https://user:password@example.test/i?edition=public&token=secret",
            error=RuntimeError("secret exception"),
        )
        return output.getvalue()

    rendered = asyncio.run(scenario())
    assert "edition=public" in rendered
    assert "password" not in rendered and "secret" not in rendered
    assert "error=UnknownError" in rendered


def test_console_sink_is_optional_for_library() -> None:
    async def scenario() -> None:
        logger = DownloadLogger()
        await logger.log("save: output.jpeg")
        await logger.close()

    asyncio.run(scenario())


def test_debug_log_is_detailed_and_chapter_log_is_separate(tmp_path: Path) -> None:
    async def scenario() -> tuple[str, str, str]:
        output = io.StringIO()
        logger = DownloadLogger([DebugFileSink(tmp_path / "debug.log"), ConsoleSink(output)])
        logger.register_chapter("1", tmp_path / "0001_Title_chapter1" / "log.log")
        await logger.log("save: image.jpeg", chapter_id="1", module="download")
        await logger.debug("response bytes=1234", chapter_id="1", module="network")
        await logger.close()
        return (
            output.getvalue(),
            (tmp_path / "0001_Title_chapter1" / "log.log").read_text(encoding="utf-8"),
            (tmp_path / "debug.log").read_text(encoding="utf-8"),
        )

    console, chapter, debug = asyncio.run(scenario())
    assert console == chapter
    assert "response bytes=1234" not in chapter
    assert "response bytes=1234" in debug
    assert "network" in debug


def test_error_detail_is_masked_and_never_written_to_console(tmp_path: Path) -> None:
    async def scenario() -> tuple[str, str, str]:
        output = io.StringIO()
        logger = DownloadLogger([DebugFileSink(tmp_path / "debug.log"), ConsoleSink(output)])
        logger.configure_safety({}, tmp_path / "downloads")
        logger.register_chapter("1", tmp_path / "downloads" / "chapter" / "log.log")
        await logger.error_detail(
            RuntimeError("request failed: https://example.test/a?session=private-value"),
            chapter_id="1",
            url="https://example.test/a?token=second-secret",
        )
        await logger.close()
        return (
            output.getvalue(),
            (tmp_path / "downloads" / "chapter" / "log.log").read_text(encoding="utf-8"),
            (tmp_path / "debug.log").read_text(encoding="utf-8"),
        )

    console, chapter, debug = asyncio.run(scenario())
    assert console == ""
    assert "private-value" not in chapter and "second-secret" not in chapter
    assert "error=UnknownError" in chapter
    assert chapter in debug
