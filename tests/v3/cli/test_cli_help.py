"""Help must describe the real argument contract without entering execution."""

from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path

import pytest

from image_downloader._cli_help import children, walk_parsers
from image_downloader.commands import dispatch, setup
from image_downloader.commands.help_catalog import COMMANDS, RUN
from image_downloader.commands.options import OPTIONS
from image_downloader.commands.parser import build_parser
from image_downloader.commands.validation import validate_arguments
from image_downloader.exceptions import ArgumentError

URL = "https://example.test/gallery"
PAGES = tuple(path for path, _ in walk_parsers(build_parser()))
LEAVES = tuple(path for path, parser in walk_parsers(build_parser()) if not children(parser))
MODES = ((), ("--inspect-only",), ("--list-updated-urls",))


def option_headers(text: str) -> set[str]:
    return set(re.findall(r"^  (--[a-z][a-z-]*)(?: [A-Z_][A-Z_=]*)?$", text, re.MULTILINE))


def show(arguments, capsys) -> str:
    with pytest.raises(SystemExit) as stopped:
        dispatch.main(arguments)
    assert stopped.value.code == 0
    output = capsys.readouterr()
    assert output.err == ""
    return output.out


@pytest.fixture
def no_execution(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("help entered configuration resolution or command execution")

    class ForbiddenHandler:
        async def handle(self, *args):
            forbidden()

    monkeypatch.setattr(setup, "_resolved_config_for", forbidden)
    for name in dispatch._COMMAND_HANDLERS:
        monkeypatch.setitem(dispatch._COMMAND_HANDLERS, name, ForbiddenHandler())


@pytest.mark.parametrize("path", PAGES)
def test_every_page_has_meaning_examples_and_equivalent_help_forms(path, capsys, no_execution):
    expected = show(["help", *path], capsys)
    assert expected.startswith("usage:")
    assert COMMANDS[" ".join(path)].summary in " ".join(expected.split())
    assert "Examples:" in expected
    assert len(COMMANDS[" ".join(path)].examples) >= 2
    assert "positional arguments:" not in expected
    assert "Default: None" not in expected and "==SUPPRESS==" not in expected
    assert expected.isascii()
    for arguments in ([*path, "--help"], ["--json", "help", *path], [*path, "-h"]):
        assert show(arguments, capsys) == expected


def test_root_is_a_command_guide_and_groups_explain_differences(capsys, no_execution):
    root = show(["help"], capsys)
    assert "Download and inspect:" in root and "Plugins and credentials:" in root
    assert not option_headers(root)
    plugin = show(["help", "plugin"], capsys)
    assert "Subcommands:" in plugin and "Options by subcommand:" in plugin
    assert "Without effect in: plugin list" in " ".join(plugin.split())
    state = show(["help", "state", "workflow"], capsys)
    assert "Omitting the subcommand is equivalent to list" in " ".join(state.split())
    assert "state workflow [SUBCOMMAND]" in state.splitlines()[0]


@pytest.mark.parametrize("mode", MODES)
def test_mode_help_is_scoped_and_does_not_echo_operands(mode, capsys, no_execution):
    expected = show(["help", "download", *mode], capsys)
    for arguments in (
        ["download", "https://example.test/private-value", *mode, "--help"],
        [*mode, "download", URL, "--help"],
        [URL, *mode, "--help"],
    ):
        actual = show(arguments, capsys)
        assert actual == expected and "private-value" not in actual
    names = option_headers(expected)
    if mode == ("--inspect-only",):
        assert {"--inspection-data", "--manifest-only"} <= names
        assert not {"--force-image-format", "--image-format", "--list-updated-urls"} & names
        ignored = expected.split("Accepted without effect:")[1].split("Examples:")[0]
        assert {"--yes", "--output-dir", "--directory-format"} <= option_headers(ignored)
    elif mode == ("--list-updated-urls",):
        assert not {"--manifest-only", "--force-image-format", "--inspect-only"} & names
        assert "independent update snapshot" in " ".join(expected.split())


def test_conflicting_modes_show_base_help_and_a_constraint(capsys, no_execution):
    text = show(["help", "download", "--inspect-only", "--list-updated-urls"], capsys)
    assert "showing normal download help" in " ".join(text.split())
    assert "--image-format" in option_headers(text)


def test_conditional_options_and_readonly_meanings(capsys, no_execution):
    target = " ".join(show(["help", "state", "workflow", "set"], capsys).split())
    assert target.count("Requires --query-targets-from-workflow-url") == 3
    assert "--status is required" in target and "per-URL confirmation" in target
    verify = " ".join(show(["help", "verify", "workflow"], capsys).split())
    assert "Inspect recorded output files" in verify and "--output-dir is required" in verify
    state = " ".join(show(["help", "state", "workflow", "history"], capsys).split())
    assert "Filter saved registrations by plugin ID" in state
    assert "live query" not in state
    assert "Display and limits:" in state and "Omitted: 20" in state
    cookies = show(["help", "cookie"], capsys)
    assert "Legacy invocation forms:" in cookies
    assert "image-downloader --export-cookies" in cookies
    assert "cannot be combined with cookie subcommands" in " ".join(cookies.split())
    assert "--chrome-user-data-dir" not in option_headers(show(["help", "cookie", "export"], capsys))


def base_arguments(path: tuple[str, ...], name: str, root: Path) -> list[str]:
    words = list(path)
    command = " ".join(path)
    if path[0] in {"download", "workflow", "inspect"}:
        words.append(URL)
    elif command == "config profile init":
        words.append("work")
    elif path[0] == "plugin" and path[1] != "list":
        words.append(str(root) if path[1] in {"install", "trust"} else "com.example.gallery")
    elif path[0] == "cookie":
        words.append("example.test" if path[1] == "browser-import" else str(root / "cookies.enc"))
    elif command in {"state workflow show", "state workflow run"}:
        words.append(RUN if path[-1] == "run" else URL)
    elif command in {"state workflow set", "state workflow reset"}:
        if name not in {"all", "url-regex"}:
            words.append(URL)
        if path[-1] == "set":
            words.extend(("--status", "pending"))
    elif path[0] == "verify":
        words.append(str(root) if path[-1] == "logs" else RUN)
        if path[-1] == "workflow":
            words.extend(("--output-dir", str(root)))
    elif path[0] == "credential" and path[-1] != "list":
        words.extend((["com.example.gallery"] if path[1] == "plugin" else []) + ["account"])
    elif path[0] == "vault" and path[-1] in {"backup", "restore"}:
        words.append(str(root / "vault.enc"))
    if command in {"state workflow targets", "state workflow set", "state workflow reset"} and name in {
        "plugin-root",
        "plugin-verification-override",
        "download-scope",
    }:
        words.extend(("--query-targets-from-workflow-url", URL))
    return words


@pytest.fixture
def values(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"plugin_id": "com.example.gallery", "config": {}}), encoding="utf-8")
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"plugin_id": "com.example.gallery", "download_policy": {}}), encoding="utf-8")
    result = {
        "config": str(tmp_path / "app.yaml"),
        "profile": "default",
        "plugin-root": str(tmp_path),
        "data-root": str(tmp_path),
        "plugin-verification-override": "bypass-signature",
        "plugin-config": "com.example.gallery={}",
        "plugin-config-file": str(config),
        "fallback-generic": "auto",
        "existing-file": "skip",
        "image-format": "PNG",
        "force-image-format": "PNG",
        "output-dir": str(tmp_path),
        "directory-format": "%CHAPTER_NUMBER%",
        "plugin": "com.example.gallery",
        "force-plugin": "com.example.gallery",
        "plugin-download-policy": "com.example.gallery={}",
        "plugin-download-policy-file": str(policy),
        "inspection-data": "url",
        "selection-priority": "0",
        "host": "example.test",
        "export-cookies": str(tmp_path / "cookies.enc"),
        "import-cookies": str(tmp_path / "cookies.enc"),
        "chrome-user-data-dir": str(tmp_path),
        "import-browser-cookies": "example.test",
        "limit": "5",
        "download-scope": "updated",
        "url-regex": "gallery",
        "filter-workflow-url": URL,
        "query-targets-from-workflow-url": URL,
        "from-status": "pending",
        "status": "pending",
        "workflow-progress-log": "enabled",
        "workflow-retries": "0",
        "workflow-retry-delay": "0",
        "workflow-retry-timeout": "1",
    }
    return result


@pytest.mark.parametrize("path", LEAVES)
def test_documented_options_match_actual_validation(path, values, tmp_path, capsys):
    _assert_acceptance(path, (), values, tmp_path, capsys)


@pytest.mark.parametrize("mode", MODES[1:])
def test_mode_options_match_actual_validation(mode, values, tmp_path, capsys):
    _assert_acceptance(("download",), mode, values, tmp_path, capsys)


def _assert_acceptance(path, mode, values, root, capsys):
    text = show(["help", *path, *mode], capsys)
    displayed = option_headers(text)
    parser = build_parser()
    parser.structured_errors = True
    for option in OPTIONS:
        argument = ["--" + option.name] + ([] if option.flag else [values[option.name]])
        words = [*base_arguments(path, option.name, root), *mode, *argument]
        try:
            validate_arguments(parser.parse_args(words))
        except ArgumentError:
            accepted = False
        else:
            accepted = True
        assert ("--" + option.name in displayed) == accepted, (path, mode, option.name, text)


def test_metadata_covers_all_registered_options():
    assert all(option.help and option.group != "Other options" for option in OPTIONS)
    assert all(option.omitted or option.flag or option.repeat or option.default is not None for option in OPTIONS)


def test_examples_have_valid_syntax_without_execution(capsys, no_execution):
    for spec in COMMANDS.values():
        for example in spec.examples:
            words = shlex.split(example)[1:]
            if words[0] == "help":
                show(words, capsys)
            else:
                build_parser().parse_args(words)


@pytest.mark.parametrize("name,path", [("root", ()), ("verify-logs", ("verify", "logs"))])
def test_help_snapshots(name, path, monkeypatch, capsys, no_execution):
    monkeypatch.setenv("COLUMNS", "80")
    monkeypatch.setattr(sys, "argv", ["image-downloader"])
    text = show(["help", *path], capsys)
    assert text == (Path(__file__).parent / "snapshots" / f"{name}.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize("width", [40, 60, 100])
def test_narrow_help_keeps_names_choices_and_examples(width, monkeypatch, capsys, no_execution):
    monkeypatch.setenv("COLUMNS", str(width))
    text = show(["help", "state", "workflow", "targets"], capsys)
    assert "--query-targets-from-workflow-url WORKFLOW_INPUT_URL" in text
    assert "bypass-signature" in text and COMMANDS["state workflow targets"].examples[1] in text
