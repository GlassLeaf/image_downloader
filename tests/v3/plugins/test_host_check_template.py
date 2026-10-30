from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from image_downloader.cli import build_parser, doctor
from image_downloader.config import AppConfig
from image_downloader.exceptions import PluginError
from image_downloader.security import PluginRuntime, install_plugin, read_manifest

SOURCE = Path(__file__).resolve().parents[3] / "examples" / "plugin-v3-host-check"
PLUGIN_ID = "com.example.host-check"


@pytest.mark.parametrize(
    ("url", "matched"),
    [
        ("https://example.test/gallery", True),
        ("http://WWW.EXAMPLE.TEST:8080/", True),
        ("https://other.test/", False),
        ("https://sub.example.test/", False),
        ("https://example.test.evil.test/", False),
        ("https://example.test@evil.test/", False),
        ("ftp://example.test/", False),
        ("example.test", False),
        ("https://[broken/", False),
    ],
)
def test_unsigned_template_matches_hosts_without_using_context(tmp_path: Path, url: str, matched: bool) -> None:
    root = (tmp_path / "plugins").resolve()
    install_plugin(root, SOURCE, mode="bypass-all")
    runtime = PluginRuntime(AppConfig(), root, mode="bypass-all")
    try:
        runtime.prepare()
        assert not [item for item in runtime.diagnostics if not item.loaded]
        plugin = runtime.site_instance(runtime.records[PLUGIN_ID])
        assert plugin.matches(url) is matched
        # An unusable context catches any attempt to request network or secrets.
        manifest = asyncio.run(plugin.inspect(url, None))
        assert manifest.chapters == ()
        assert manifest.allow_empty_chapters is True
        assert plugin.auth_flow(None) is None
    finally:
        runtime.close()


def test_unsigned_template_is_rejected_in_strict_mode() -> None:
    with pytest.raises(PluginError, match="wrapper"):
        read_manifest(SOURCE)


@pytest.mark.parametrize(("host", "matched"), [("example.test", True), ("other.test", False)])
def test_doctor_reports_host_match(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], host: str, matched: bool
) -> None:
    root = (tmp_path / "plugins").resolve()
    install_plugin(root, SOURCE, mode="bypass-all")
    args = build_parser().parse_args(
        [
            "doctor",
            "--plugin-root",
            str(root),
            "--data-root",
            str((tmp_path / "data").resolve()),
            "--plugin-verification-override",
            "bypass-all",
            "--fallback-generic",
            "enabled",
            "--host",
            host,
            "--json",
        ]
    )
    assert asyncio.run(doctor(args, raise_errors=True)) == 0
    output = capsys.readouterr().out
    result = json.loads(output)["execution"]["result"]
    detail = next(item for item in result["loaded_plugins"] if item["id"] == PLUGIN_ID)
    assert detail["publisher"] is None
    assert detail["key_id"] is None
    assert detail["capabilities"] == []
    selection = result["configuration"]["selection"]
    assert next(item for item in selection if item["id"] == PLUGIN_ID)["matched"] is matched
    assert not (tmp_path / "data").exists()
