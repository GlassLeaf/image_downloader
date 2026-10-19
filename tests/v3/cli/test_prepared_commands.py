"""Validation hands immutable, command-specific inputs to execution."""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError

import pytest

from image_downloader.commands import dispatch
from image_downloader.commands.parser import build_parser
from image_downloader.commands.prepared import DownloadArguments, PluginArguments
from image_downloader.commands.validation import validate_arguments


def test_download_input_is_detached_from_namespace_and_nested_override_values():
    args = build_parser().parse_args(
        ["download", "https://example.test/gallery", "--plugin-config", 'example.site={"items":[1,2]}']
    )
    command = validate_arguments(args)
    assert isinstance(command.arguments, DownloadArguments)
    args.url = "not-a-url"
    args.plugin_config.append('example.site={"items":[3]}')
    assert command.arguments.url == "https://example.test/gallery"
    assert command.plugin_config["example.site"]["items"] == (1, 2)
    with pytest.raises(FrozenInstanceError):
        command.arguments.url = "changed"
    assert not hasattr(command.arguments, "workflow_retries")
    assert not hasattr(command.arguments, "_explicit_options")


def test_plugin_execution_does_not_read_changed_positional_parser_state(monkeypatch):
    args = build_parser().parse_args(["plugin", "list"])
    command = validate_arguments(args)
    assert isinstance(command.arguments, PluginArguments)
    args.command_args[:] = ["uninstall", "example.other"]
    assert command.arguments.command_args == ("list",)
    with pytest.raises(FrozenInstanceError):
        command.arguments.command_args = ("uninstall", "example.other")

    class Handler:
        async def handle(self, prepared, outcome):
            args.command_args[:] = ["uninstall", "example.other"]
            assert prepared.arguments.command_args == ("list",)
            return 37

    monkeypatch.setitem(dispatch._COMMAND_HANDLERS, "plugin", Handler())
    args.command_args[:] = ["list"]
    assert asyncio.run(dispatch.run(args)) == 37
