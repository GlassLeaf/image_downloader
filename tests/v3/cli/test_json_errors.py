from __future__ import annotations

import json

from image_downloader.commands import dispatch
from image_downloader.exceptions import PluginError


def test_json_syntax_error_uses_the_fixed_error_envelope(capsys) -> None:
    status = dispatch.main(["download", "--json"])

    output = capsys.readouterr()
    assert status == 2
    assert output.err == ""
    assert json.loads(output.out) == {
        "error": {
            "code": "configuration_error",
            "reason": "configuration is invalid",
            "exception": "ConfigurationError",
            "message": "configuration is invalid",
            "operation": "download",
        }
    }


def test_json_runtime_error_is_safe_and_preserves_plugin_exit_status(monkeypatch, capsys) -> None:
    class FailingHandler:
        async def handle(self, _args: object) -> int:
            raise PluginError("token=do-not-expose")

    monkeypatch.setitem(dispatch._COMMAND_HANDLERS, "doctor", FailingHandler())

    status = dispatch.main(["doctor", "--json"])

    output = capsys.readouterr()
    assert status == 4
    assert output.err == ""
    assert json.loads(output.out) == {
        "error": {
            "code": "plugin_error",
            "reason": "plugin operation failed",
            "exception": "PluginError",
            "message": "plugin operation failed",
            "operation": "doctor",
        }
    }
    assert "do-not-expose" not in output.out
