from __future__ import annotations

import asyncio
import importlib
import subprocess
import sys
from pathlib import Path

from image_downloader import cli


def test_installed_entry_point_plugin_runs_through_cli(tmp_path: Path, monkeypatch) -> None:
    package = tmp_path / "external_plugin"
    package.mkdir()
    (package / "external_plugin.py").write_text(
        "from image_downloader.models import ParseResult\n"
        "from image_downloader.plugins import BaseDownloader\n"
        "class ExternalPlugin(BaseDownloader):\n"
        "    priority = 10000\n"
        "    @classmethod\n"
        "    def can_handle(cls, url): return url.startswith('http://external.test/')\n"
        "    async def parse(self, url, context=None): return ParseResult('external', [])\n",
        encoding="utf-8",
    )
    (package / "setup.py").write_text(
        "from setuptools import setup\n"
        "setup(name='image-downloader-external-fixture', version='0.0.0', py_modules=['external_plugin'], "
        "entry_points={'image_downloader.plugins': ['external=external_plugin:ExternalPlugin']})\n",
        encoding="utf-8",
    )
    installed = tmp_path / "installed"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--no-build-isolation",
            "--target",
            str(installed),
            str(package),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    monkeypatch.syspath_prepend(str(installed))
    importlib.invalidate_caches()

    config_path = tmp_path / "app.yaml"
    config_path.write_text("", encoding="utf-8")

    args = cli.build_parser().parse_args(
        [
            "http://external.test/page",
            "--config",
            str(config_path),
            "--no-console-log",
        ]
    )
    assert asyncio.run(cli.run(args)) == cli.EXIT_SUCCESS
