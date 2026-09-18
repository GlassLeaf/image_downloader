from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from image_downloader import cli
from image_downloader.models import DownloadResult


class PartialDownloader:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def download(self) -> DownloadResult:
        return DownloadResult("https://example.test", ["saved.jpeg"], ["failed.jpeg: HTTP 500"])

    async def close(self) -> None:
        pass


def test_cli_returns_partial_exit_code(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "Downloader", PartialDownloader)
    args = argparse.Namespace(
        url="https://example.test/page",
        config=tmp_path / "app.yaml",
        log_path=None,
        no_console_log=True,
        list_updated_urls=False,
        profile=None,
        existing_file=None,
        image_format=None,
    )
    assert asyncio.run(cli.run(args)) == cli.EXIT_PARTIAL
