"""CLI shutdown warnings must not replace the operation's structured failure."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from support.workflow import FEED, Gallery, compose

from image_downloader import AuthenticationError, ConfigurationError, PluginError
from image_downloader.commands import download, inspect
from image_downloader.commands.constants import EXIT_AUTHENTICATION, EXIT_CONFIGURATION, EXIT_PLUGIN
from image_downloader.commands.dispatch import main


@pytest.mark.parametrize("command", ["download", "inspect"])
@pytest.mark.parametrize(
    "error_type,status",
    [(AuthenticationError, EXIT_AUTHENTICATION), (ConfigurationError, EXIT_CONFIGURATION), (PluginError, EXIT_PLUGIN)],
)
def test_close_failure_keeps_cli_error_code_and_json(tmp_path, monkeypatch, capsys, command, error_type, status):
    service = compose(tmp_path, Gallery())
    primary = error_type("token=primary-secret")
    original = service.gateway.close

    async def execute(_request):
        raise primary

    async def close():
        await original()
        raise RuntimeError("token=secondary-secret")

    module = download if command == "download" else inspect
    composer = SimpleNamespace(compose=lambda: service, _compose_for_inspection=lambda: service)
    monkeypatch.setattr(module, "RuntimeComposer", lambda *_args, **_kwargs: composer)
    monkeypatch.setattr(module, "_config_for", lambda *_args, **_kwargs: (service.config, tmp_path, {}, None))
    if command == "download":
        monkeypatch.setattr(module, "_persist_initial_user_config", lambda _source: None)
    monkeypatch.setattr(service, command, execute)
    monkeypatch.setattr(service.gateway, "close", close)
    assert main([command, FEED, "--json"]) == status
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert payload["error"]["code"] == primary.code
    assert payload["error"]["exception"] == error_type.__name__
    assert "service cleanup failed" in output.err
    assert "secret" not in output.out + output.err
    assert service.gateway.client.is_closed and service.image_processor._closed
