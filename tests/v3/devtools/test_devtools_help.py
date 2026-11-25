from __future__ import annotations

import re
import shlex

import pytest

from image_downloader._cli_help import children
from image_downloader.devtools import cli, signing
from image_downloader.devtools.help import COMMANDS

PAGES = ((), *((name,) for name in children(cli.parser())))


@pytest.fixture
def no_execution(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("development help entered execution, generation, file loading, or signing")

    for name in ("WorkerClient", "generation_command", "execution_command", "read_document"):
        monkeypatch.setattr(cli, name, forbidden)
    monkeypatch.setattr(signing, "sign", forbidden)
    monkeypatch.setattr(signing, "load_key", forbidden)
    monkeypatch.chdir(tmp_path)
    yield
    assert list(tmp_path.iterdir()) == []


def show(arguments, capsys):
    with pytest.raises(SystemExit) as stopped:
        cli.main(arguments)
    assert stopped.value.code == 0
    output = capsys.readouterr()
    assert output.err == ""
    return output.out


@pytest.mark.parametrize("path", PAGES)
def test_help_forms_are_equal_and_complete(path, capsys, no_execution):
    expected = show(["help", *path], capsys)
    name = " ".join(path)
    assert expected.startswith("usage:")
    assert COMMANDS[name].summary in " ".join(expected.split())
    assert len(COMMANDS[name].examples) >= 2 and "Examples:" in expected
    assert expected.isascii() and "Default: None" not in expected and "==SUPPRESS==" not in expected
    for arguments in ([*path, "--help"], [*path, "-h"], ["--json", "help", *path], ["help", *path, "--json"]):
        assert show(arguments, capsys) == expected
    parser = cli.parser()
    if path:
        parser = children(parser)[path[0]]
        names = {a.option_strings[0] for a in parser._actions if a.option_strings and a.dest != "help"}
        displayed = set(re.findall(r"^  (--[a-z][a-z-]*)(?: [A-Z_][A-Z_=]*)?$", expected, re.MULTILINE))
        assert displayed == names
    else:
        assert "Generate and sign:" in expected and "--timeout" not in expected


def test_sign_and_call_explain_required_flags_and_input_precedence(capsys, no_execution):
    sign = " ".join(show(["help", "sign"], capsys).split())
    assert "--key is required" in sign and "outside the plugin source tree" in sign
    call = " ".join(show(["help", "call"], capsys).split())
    assert "--input replaces --args/--kwargs" in call
    assert "do not persist between separate CLI invocations" in call
    session = " ".join(show(["help", "session"], capsys).split())
    assert all(name in session for name in ("refs", "reload", "simulate URL", "quit", "save_as"))
    assert "Console filters do not change stored diagnostics" in session


def test_examples_parse_without_running_them(capsys, no_execution):
    for spec in COMMANDS.values():
        for example in spec.examples:
            words = shlex.split(example)[1:]
            if words[0] == "help":
                show(words, capsys)
            else:
                cli.parser().parse_args(words)


@pytest.mark.parametrize("words", [["help", "private-value"], ["help", "call", "private-value"]])
def test_invalid_alias_targets_do_not_echo_values(words, capsys, no_execution):
    with pytest.raises(SystemExit) as stopped:
        cli.main(words)
    assert stopped.value.code == 2
    output = capsys.readouterr()
    assert "private-value" not in output.out + output.err


def test_help_never_echoes_target_or_option_values(capsys, no_execution):
    text = show(["call", "private-target", "private-method", "--args", '["private-secret"]', "--help"], capsys)
    assert "private-" not in text
