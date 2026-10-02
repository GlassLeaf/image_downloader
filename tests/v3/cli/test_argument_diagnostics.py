from __future__ import annotations

import asyncio
import itertools
import json

import pytest

from image_downloader import ArgumentError, ConfigurationError
from image_downloader.commands import dispatch, setup
from image_downloader.commands.options import OPTIONS
from image_downloader.commands.parser import build_parser
from image_downloader.commands.validation import validate_arguments
from image_downloader.diagnostics import argument_error, diagnostic_for
from image_downloader.exceptions import ErrorInfo, error_info_for

URL = "https://example.test/feed?token=private-value"


@pytest.fixture
def no_execution(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid arguments/help must not enter execution or configuration resolution")

    class ForbiddenHandler:
        async def handle(self, args):
            forbidden()

    monkeypatch.setattr(setup, "_resolved_config_for", forbidden)
    for name in dispatch._COMMAND_HANDLERS:
        monkeypatch.setitem(dispatch._COMMAND_HANDLERS, name, ForbiddenHandler())


@pytest.mark.parametrize(
    "words,kind,operation,fragment",
    [
        ([], "missing_command", "cli", "command"),
        (["--list"], "unknown_option", "cli", "--list"),
        (["--j"], "unknown_option", "cli", "--j"),
        (["--unknown-option=private-value"], "unknown_option", "cli", "--unknown-option"),
        (["private-value"], "unknown_command", "cli", "choose from"),
        (["plugin"], "missing_argument", "plugin", "required"),
        (["plugin", "private-value"], "unknown_command", "plugin", "choose from"),
        (["plugin", "install"], "missing_argument", "plugin", "target"),
        (["config", "profile", "init"], "missing_argument", "config", "profile_name"),
        (["download"], "missing_argument", "download", "url"),
        (["download", URL, "private-value"], "unexpected_argument", "download", "positional"),
        (["--host"], "missing_value", "cli", "--host"),
        (["doctor", "--host", ""], "invalid_value", "doctor", "--host"),
        (["--config", "plugin", "doctor"], "invalid_value", "doctor", "--config"),
        (["workflow", URL, "--workflow-retries", "private-value"], "invalid_value", "workflow", "integer"),
        (["workflow", URL, "--workflow-retry-delay", "-1e3"], "invalid_value", "workflow", "non-negative"),
        (["doctor", "--image-format", "private-value"], "invalid_value", "doctor", "choose from"),
    ],
)
def test_specific_single_json_diagnostic(words, kind, operation, fragment, capsys, no_execution):
    assert dispatch.main([*words, "--json"]) == 2
    captured = capsys.readouterr()
    assert captured.err == ""
    payload = json.loads(captured.out)["error"]
    assert payload["code"] == "argument_error"
    assert payload["reason"] == "command-line arguments are invalid"
    assert payload["exception"] == "ArgumentError"
    assert payload["operation"] == operation
    assert payload["details"]["kind"] == kind
    assert fragment in payload["message"]
    assert "private-value" not in captured.out


@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize(
    "command,options",
    [
        (["plugin", "list"], ["--host", "example.test"]),
        (["plugin", "list"], ["--selection-priority", "0"]),
        (["config", "path"], ["--fallback-generic", "auto"]),
        (["state", "workflow", "history"], ["--selection-priority", "0"]),
        (["state", "workflow", "list"], ["--limit", "20"]),
        (["download", URL], ["--inspection-data", "url"]),
        (["workflow", URL], ["--inspection-data", "url"]),
        (["cookie", "export", "cookies.export"], ["--manifest-only"]),
        (["doctor"], ["--inspect-only"]),
    ],
)
def test_scope_error_is_identical_at_every_position(command, options, json_mode, capsys, no_execution):
    results = []
    for position in range(len(command) + 1):
        words = [*command[:position], *options, *command[position:]]
        if json_mode:
            words.append("--json")
        assert dispatch.main(words) == 2
        captured = capsys.readouterr()
        results.append((captured.out, captured.err))
    assert len(set(results)) == 1


@pytest.mark.parametrize(
    "command,left,right",
    [
        (["download", URL], ["--image-format", "PNG"], ["--force-image-format", "JPEG"]),
        (["workflow", URL], ["--plugin", "test.a"], ["--force-plugin", "test.b"]),
        (["download", URL], ["--inspect-only"], ["--list-updated-urls"]),
        (["download", URL], ["--plugin", "test.a"], ["--fallback-generic", "enabled"]),
        (["inspect", URL], ["--force-plugin", "test.a"], ["--fallback-generic=disabled"]),
        ([], ["--export-cookies", "cookies.export"], ["--import-browser-cookies", "example.test"]),
        (["cookie", "export", "cookies.export"], ["--import-cookies", "cookies.export"], []),
    ],
)
def test_conflicts_are_independent_of_level_and_order(command, left, right, capsys, no_execution):
    results = []
    for a, b in itertools.permutations((left, right)):
        for position in range(len(command) + 1):
            words = [*a, *command[:position], *b, *command[position:], "--json"]
            assert dispatch.main(words) == 2
            payload = json.loads(capsys.readouterr().out)["error"]
            assert payload["details"]["kind"] == "option_conflict"
            results.append(payload)
    assert all(value == results[0] for value in results)


@pytest.mark.parametrize("words", [[], ["plugin"], ["config", "profile"], ["state", "workflow"], ["cookie", "export"]])
@pytest.mark.parametrize("json_mode", [False, True])
def test_help_alias_matches_flag_and_has_no_side_effects(words, json_mode, capsys, no_execution):
    prefix = ["--json"] if json_mode else []
    outputs = []
    for arguments in ([*prefix, "help", *words], [*prefix, *words, "--help"]):
        with pytest.raises(SystemExit) as stopped:
            dispatch.main(arguments)
        assert stopped.value.code == 0
        captured = capsys.readouterr()
        assert captured.err == ""
        assert captured.out.startswith("usage:")
        outputs.append(captured.out)
    assert outputs[0] == outputs[1]


@pytest.mark.parametrize(
    "words",
    [["help", "private-value"], ["help", "config", "profile", "init", "private-value"], ["--host", "--help", "--json"]],
)
def test_invalid_help_input_is_structured(words, capsys, no_execution):
    assert dispatch.main(["--json", *words]) == 2
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out)["error"]["code"] == "argument_error"
    assert "private-value" not in captured.out


def test_help_stops_before_later_errors_and_handles_leaf_operands(capsys, no_execution):
    for words in (
        ["--help", "--unknown-option"],
        ["plugin", "install", "private-value", "--help"],
        ["download", URL, "-h"],
    ):
        with pytest.raises(SystemExit) as stopped:
            dispatch.main(words)
        assert stopped.value.code == 0
        assert "private-value" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "operand",
    [
        "",
        "http://",
        "https:///path",
        "ftp://example.test/path",
        "file:///tmp/file",
        "https://example.test:invalid/path",
        "https://example.test:0/path",
        "https://example.test/a b",
    ],
)
@pytest.mark.parametrize("command", ["download", "inspect", "workflow"])
def test_absolute_http_url_is_required(command, operand, capsys, no_execution):
    assert dispatch.main([command, operand, "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)["error"]
    assert payload["details"] == {"kind": "invalid_value", "argument": "url"}


def test_order_duplicates_empty_values_and_command_named_values(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    args = build_parser().parse_args(["plugin", "install", "--yes", str(source), "--selection-priority=0"])
    assert args.command_args == ["install", str(source)]
    assert validate_arguments(args) == "plugin"
    args = build_parser().parse_args(["config", "profile", "--json", "init", "new_profile"])
    assert args.command_args == ["profile", "init", "new_profile"]
    args = build_parser().parse_args(
        [
            "--profile",
            "plugin",
            "download",
            URL,
            "--profile=default",
            "--directory-format=",
            "--plugin-config",
            'test.a={"one":1}',
            '--plugin-config=test.a={"two":2}',
        ]
    )
    assert args.command == "download" and args.profile == "default"
    assert args.directory_format == ""
    assert args.plugin_config == ['test.a={"one":1}', 'test.a={"two":2}']
    assert args._option_order == ("--profile", "--profile", "--directory-format", "--plugin-config", "--plugin-config")
    validate_arguments(args)


def test_files_merge_before_inline_in_their_respective_orders(tmp_path):
    files = []
    for name, value in (("first", {"a": 1, "nested": {"file": 1}}), ("second", {"a": 2})):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps({"plugin_id": "test.a", "config": value}), encoding="utf-8")
        files.append(str(path))
    args = build_parser().parse_args(
        [
            "--plugin-config",
            'test.a={"a":3}',
            "download",
            URL,
            "--plugin-config-file",
            files[0],
            "--plugin-config",
            'test.a={"nested":{"inline":2}}',
            "--plugin-config-file",
            files[1],
        ]
    )
    assert setup._runtime_overrides(args) == {"test.a": {"a": 3, "nested": {"file": 1, "inline": 2}}}


def test_terminator_and_option_values_never_select_json_or_commands(capsys, no_execution):
    assert dispatch.main(["download", URL, "--", "--json"]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "positional" in captured.err
    assert dispatch.main(["--json", "--config=plugin", "private-value"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["operation"] == "cli"
    args = build_parser().parse_args(["--", URL])
    assert args.command_handler == "download" and not args.json_output
    args = build_parser().parse_args(["state", "workflow", "show", "--", "--json"])
    assert args.command_args == ["workflow", "show", "--json"] and not args.json_output


def test_precedence_is_structure_then_scope_then_conflict_then_value(capsys, no_execution):
    cases = [
        (["plugin", "unknown", "--limit=private-value"], "unknown_command"),
        (["plugin", "list", "--limit=private-value"], "option_scope"),
        (
            ["workflow", URL, "--plugin=test.a", "--force-plugin=test.b", "--workflow-retries=private-value"],
            "option_conflict",
        ),
        (["workflow", URL, "--workflow-retries=private-value"], "invalid_value"),
    ]
    for words, kind in cases:
        assert dispatch.main([*words, "--json"]) == 2
        assert json.loads(capsys.readouterr().out)["error"]["details"]["kind"] == kind


@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize(
    "content,kind,fragment",
    [
        (None, "configuration_missing", "not found"),
        ("network: [private-value", "configuration_syntax", "YAML"),
        (
            "network:\n  request_concurrency: 0\n  headers: {Authorization: private-value}\n",
            "configuration_value",
            "network.request_concurrency must be greater than or equal to 1",
        ),
    ],
)
def test_configuration_diagnostics_are_specific_and_private(tmp_path, content, kind, fragment, json_mode, capsys):
    path = tmp_path / "app.yaml"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    words = ["config", "explain", "--config", str(path)] + (["--json"] if json_mode else [])
    assert dispatch.main(words) == 2
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert fragment in text
    assert "private-value" not in text and str(tmp_path) not in text
    if json_mode:
        assert captured.err == ""
        payload = json.loads(captured.out)["error"]
        assert payload["code"] == "configuration_error"
        assert payload["details"]["kind"] == kind
        if content is not None:
            assert payload["details"]["source"] == "app:app.yaml"
            assert payload["details"]["line"] >= 1 and payload["details"]["column"] >= 1


def test_public_exception_and_shared_error_info_stay_compatible():
    error = argument_error("--host requires a value", "missing_value", option="--host")
    assert isinstance(error, (ArgumentError, ConfigurationError))
    assert diagnostic_for(error) is not None
    assert error_info_for(error) == ErrorInfo(
        "argument_error", ArgumentError.reason, "ArgumentError", ArgumentError.reason
    )
    assert diagnostic_for(ConfigurationError("private-value")) is None


def test_unknown_value_error_is_not_reclassified_as_configuration(monkeypatch, capsys):
    class BrokenHandler:
        async def handle(self, args):
            raise ValueError("private-value")

    monkeypatch.setitem(dispatch._COMMAND_HANDLERS, "doctor", BrokenHandler())
    assert dispatch.main(["doctor", "--json"]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["error"]["code"] == "unexpected_runtime_error"
    assert "private-value" not in captured.out


@pytest.mark.parametrize(
    "words",
    [["--unknown-option"], ["download"], ["plugin", "unknown"], ["workflow", URL, "--workflow-retries=private-value"]],
)
@pytest.mark.parametrize("json_mode", [False, True])
def test_direct_parser_syntax_errors_keep_system_exit(words, json_mode):
    with pytest.raises(SystemExit) as stopped:
        build_parser().parse_args([*words, *(["--json"] if json_mode else [])])
    assert stopped.value.code == 2


def test_registry_has_one_definition_per_option():
    parser = build_parser()
    names = ["--" + option.name for option in OPTIONS]
    assert len(names) == len(set(names))
    assert set(parser._option_string_actions) == set(names) | {"--help", "-h"}


def test_validation_passes_stop_at_handler_boundary(monkeypatch):
    seen = []

    class Boundary:
        async def handle(self, args):
            seen.append(args.command_handler)
            return 31

    monkeypatch.setitem(dispatch._COMMAND_HANDLERS, "download", Boundary())
    assert asyncio.run(dispatch.run(build_parser().parse_args([URL]))) == 31
    assert seen == ["download"]


def test_override_file_is_validated_once_and_namespace_can_be_reused(tmp_path, monkeypatch):
    path = tmp_path / "overrides.json"
    path.write_text('{"plugin_id":"test.a","config":{"version":1}}', encoding="utf-8")
    args = build_parser().parse_args(["download", URL, "--plugin-config-file", str(path)])
    seen = []

    class Boundary:
        async def handle(self, args):
            seen.append(setup._runtime_overrides(args))
            path.write_text("private-value is invalid JSON", encoding="utf-8")
            assert setup._runtime_overrides(args) == seen[-1]
            return 0

    monkeypatch.setitem(dispatch._COMMAND_HANDLERS, "download", Boundary())
    assert asyncio.run(dispatch.run(args)) == 0
    assert not hasattr(args, "_prepared_plugin_config") and not hasattr(args, "_arguments_validated")
    with pytest.raises(ArgumentError, match="must contain valid JSON"):
        asyncio.run(dispatch.run(args))
    assert seen == [{"test.a": {"version": 1}}]


@pytest.mark.parametrize("content", ["[]", "false", "private-value", "network: {headers: {private-value: []}}"])
def test_nonmapping_and_dynamic_field_diagnostics_do_not_expose_values(tmp_path, content, capsys):
    path = tmp_path / "app.yaml"
    path.write_text(content, encoding="utf-8")
    assert dispatch.main(["config", "explain", "--config", str(path), "--json"]) == 2
    captured = capsys.readouterr()
    assert "private-value" not in captured.out + captured.err
    assert json.loads(captured.out)["error"]["code"] == "configuration_error"


def test_policy_constraints_name_the_field_before_any_execution(capsys, no_execution):
    assert (
        dispatch.main(
            [
                "download",
                URL,
                "--plugin-download-policy",
                'test.a={"request_concurrency":0,"private-value":1}',
                "--json",
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)["error"]
    assert payload["code"] == "argument_error"
    assert payload["details"]["field"] == "request_concurrency"
    assert "greater than or equal to 1" in payload["message"]
    assert "private-value" not in captured.out


def test_unknown_option_does_not_establish_a_command_from_its_possible_value(capsys, no_execution):
    assert dispatch.main(["--unknown-option", "plugin", "list", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["operation"] == "cli"


@pytest.mark.parametrize(
    "words",
    [
        ["download", URL, "--plugin="],
        ["inspect", URL, "--force-plugin="],
        ["cookie", "export", ""],
        ["cookie", "browser-import", ""],
        ["--export-cookies="],
        ["--import-cookies="],
        ["--import-browser-cookies="],
        ["plugin", "revoke", ""],
        ["plugin", "uninstall", ""],
    ],
)
def test_empty_operation_values_fail_before_cookie_or_plugin_access(words, capsys, no_execution):
    assert dispatch.main([*words, "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)["error"]
    assert payload["code"] == "argument_error" and "non-empty" in payload["message"]
