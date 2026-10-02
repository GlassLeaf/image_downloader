"""Exercise future definitions without shipping new CLI features or doing I/O."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from textwrap import dedent
from typing import Literal

import pytest
from pydantic import Field, StrictInt, ValidationError

from image_downloader.commands import dispatch, inspect, options, parser, setup, validation
from image_downloader.configuration import layers
from image_downloader.configuration.models import AppConfig, Media, Network
from image_downloader.diagnostics import diagnostic_for
from image_downloader.exceptions import ArgumentError, ConfigurationError


@pytest.fixture
def extended_options(monkeypatch):
    scope = frozenset({"plugin list", "doctor"})
    additions = (
        options.OptionDefinition("future-flag", scope, default=False, flag=True),
        options.OptionDefinition("future-count", scope, int, default=5),
        options.OptionDefinition("future-ratio", scope, float),
        options.OptionDefinition("future-mode", scope, choices=("basic", "extended", "new-choice")),
        options.OptionDefinition("future-tag", scope, repeat=True, dest="future_items"),
        options.OptionDefinition("future-file", scope, Path, repeat=True),
        options.OptionDefinition("future-named", scope, dest="future_alias"),
        options.OptionDefinition("future-hidden", scope, default=argparse.SUPPRESS),
    )
    definitions = (*options.OPTIONS, *additions)
    # Mirror the derived bindings that a fresh process creates after extending
    # options.py; changing just one already-imported module isn't registration.
    monkeypatch.setattr(options, "OPTIONS", definitions)
    monkeypatch.setattr(parser, "OPTIONS", definitions)
    monkeypatch.setattr(validation, "OPTIONS", definitions)
    monkeypatch.setattr(validation, "_OPTION_NAMES", tuple(option.name for option in definitions))
    monkeypatch.setattr(validation, "_OPTIONS_BY_NAME", {option.name: option for option in definitions})
    monkeypatch.setattr(
        validation,
        "_OPTION_DEFAULTS",
        {
            option.attribute: [] if option.repeat else None if option.default == argparse.SUPPRESS else option.default
            for option in definitions
        },
    )

    def forbidden(*args, **kwargs):
        pytest.fail("definition validation/help must not initialize configuration or execute commands")

    class ForbiddenHandler:
        async def handle(self, args):
            forbidden()

    monkeypatch.setattr(setup, "_resolved_config_for", forbidden)
    for name in dispatch._COMMAND_HANDLERS:
        monkeypatch.setitem(dispatch._COMMAND_HANDLERS, name, ForbiddenHandler())
    return additions


@pytest.mark.parametrize("position", [0, 1, 2])
@pytest.mark.parametrize(
    "words,attribute,expected",
    [
        (["--future-flag"], "future_flag", True),
        (["--future-count", "-4"], "future_count", -4),
        (["--future-count=0"], "future_count", 0),
        (["--future-ratio", "-1e3"], "future_ratio", -1000.0),
        (["--future-mode", "new-choice"], "future_mode", "new-choice"),
        (["--future-tag", "plugin"], "future_items", ["plugin"]),
        (["--future-file", "plugin"], "future_file", [Path("plugin")]),
        (["--future-named=doctor"], "future_alias", "doctor"),
        (["--future-hidden=plugin"], "future_hidden", "plugin"),
    ],
)
def test_added_options_are_position_independent(extended_options, position, words, attribute, expected):
    command = ["plugin", "list"]
    args = parser.build_parser().parse_args([*command[:position], *words, *command[position:]])
    assert validation.validate_arguments(args) == "plugin"
    assert args.command_args == ["list"]
    assert getattr(args, attribute) == expected


def test_added_repeat_options_keep_order_and_scalar_duplicates_use_last(extended_options):
    args = parser.build_parser().parse_args(
        [
            "--future-tag=first",
            "--future-file=first.json",
            "--future-count=1",
            "plugin",
            "--future-tag=plugin",
            "list",
            "--future-file=second.json",
            "--future-tag=last",
            "--future-count=5",
            "--future-named=",
        ]
    )
    assert validation.validate_arguments(args) == "plugin"
    assert args.future_items == ["first", "plugin", "last"]
    assert args.future_file == [Path("first.json"), Path("second.json")]
    assert args.future_count == 5 and args.future_alias == ""
    assert args._option_order[-2:] == ("--future-count", "--future-named")


@pytest.mark.parametrize("position", [0, 1, 2])
@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize(
    "words,kind,option",
    [
        (["--future-count=private-value"], "invalid_value", "--future-count"),
        (["--future-ratio=private-value"], "invalid_value", "--future-ratio"),
        (["--future-mode=private-value"], "invalid_value", "--future-mode"),
        (["--future-mode="], "invalid_value", "--future-mode"),
        (["--future-c=private-value"], "unknown_option", "--future-c"),
    ],
)
def test_added_option_errors_remain_specific_and_private(
    extended_options, position, json_mode, words, kind, option, capsys
):
    command = ["plugin", "list"]
    argv = [*command[:position], *words, *command[position:], *(["--json"] if json_mode else [])]
    assert dispatch.main(argv) == 2
    captured = capsys.readouterr()
    assert "private-value" not in captured.out + captured.err
    if json_mode:
        assert captured.err == ""
        payload = json.loads(captured.out)["error"]
        assert payload["code"] == "argument_error"
        assert payload["details"]["kind"] == kind
        assert payload["details"]["option"] == option
        if option == "--future-mode":
            assert payload["details"]["choices"] == ["basic", "extended", "new-choice"]
    else:
        assert captured.out == ""
        assert captured.err.startswith("error [argument_error]:") and option in captured.err


@pytest.mark.parametrize("words", [["--future-count"], ["plugin", "list", "--future-named", "--help"]])
def test_added_option_missing_value_precedes_help(extended_options, words, capsys):
    assert dispatch.main([*words, "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)["error"]
    assert payload["details"]["kind"] == "missing_value"


@pytest.mark.parametrize("position", [0, 1, 2])
@pytest.mark.parametrize("value", ["0", "5", "private-value"])
def test_added_option_scope_precedes_value_even_at_default(extended_options, position, value, capsys):
    command = ["config", "path"]
    argv = [*command[:position], f"--future-count={value}", *command[position:], "--json"]
    assert dispatch.main(argv) == 2
    payload = json.loads(capsys.readouterr().out)["error"]
    assert payload["details"] == {"kind": "option_scope", "option": "--future-count", "command": "config path"}


@pytest.mark.parametrize("reverse", [False, True])
def test_added_conflict_is_detected_across_hierarchy_before_values(extended_options, monkeypatch, reverse, capsys):
    group = ("future-count", "future-mode")
    monkeypatch.setattr(validation, "CONFLICT_GROUPS", (*options.CONFLICT_GROUPS, group))
    pair = ["--future-count=private-value", "--future-mode=private-value"]
    if reverse:
        pair.reverse()
    assert dispatch.main([pair[0], "plugin", "list", pair[1], "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)["error"]
    assert payload["details"] == {"kind": "option_conflict", "options": ["--future-count", "--future-mode"]}


def test_added_options_appear_in_help_without_initialization(extended_options, capsys):
    with pytest.raises(SystemExit) as stopped:
        dispatch.main(["help", "--json"])
    assert stopped.value.code == 0
    output = capsys.readouterr()
    assert output.err == ""
    for spec in extended_options:
        assert "--" + spec.name in output.out
    assert "new-choice" in output.out


def test_added_options_respect_terminator(extended_options):
    args = parser.build_parser().parse_args(["state", "workflow", "show", "--", "--future-flag"])
    assert validation.validate_arguments(args) == "state"
    assert args.target == "--future-flag" and not args.future_flag


@pytest.mark.parametrize("reverse", [False, True])
def test_added_invalid_values_use_definition_order(extended_options, reverse, capsys):
    words = ["--future-count=private-value", "--future-ratio=private-value"]
    if reverse:
        words.reverse()
    assert dispatch.main(["plugin", "list", *words, "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["details"]["option"] == "--future-count"


def test_new_registry_definition_works_on_fresh_import_without_other_edits():
    root = Path(__file__).resolve().parents[3]
    code = dedent("""\
        import contextlib
        import io
        import json
        from image_downloader.commands import options
        options.OPTIONS += (options.OptionDefinition(
            "future-fresh", frozenset({"plugin list"}),
            choices=("basic", "new-choice"), dest="future_destination",
        ),)
        from image_downloader.commands import dispatch, parser, validation
        args = parser.build_parser().parse_args(["plugin", "--future-fresh=new-choice", "list"])
        accepted = validation.validate_arguments(args)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = dispatch.main(["config", "--future-fresh=basic", "path", "--json"])
        print(json.dumps({"accepted": accepted, "value": args.future_destination,
                          "status": status, "error": json.loads(output.getvalue())["error"]}))
        """)
    process = subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root / "src")},
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    payload = json.loads(process.stdout)
    assert process.stderr == ""
    assert payload["accepted"] == "plugin" and payload["value"] == "new-choice"
    assert payload["status"] == 2
    assert payload["error"]["details"] == {
        "kind": "option_scope",
        "option": "--future-fresh",
        "command": "config path",
    }


@pytest.mark.parametrize("attribute,value", [("future_alias", "plugin"), ("future_count", 0)])
def test_manual_namespace_uses_registered_destination_and_default(extended_options, attribute, value):
    args = parser.build_parser().parse_args(["config", "path"])
    del args._explicit_options
    setattr(args, attribute, value)
    with pytest.raises(ArgumentError) as caught:
        validation.validate_arguments(args)
    diagnostic = diagnostic_for(caught.value)
    assert diagnostic is not None
    assert diagnostic.details["kind"] == "option_scope"


def test_manual_namespace_does_not_treat_registered_defaults_as_explicit(extended_options):
    args = parser.build_parser().parse_args(["config", "path"])
    del args._explicit_options
    assert args.future_count == 5
    assert validation.validate_arguments(args) == "config"


@pytest.fixture
def extended_config(monkeypatch):
    class ExtendedNetwork(Network):
        future_capacity: StrictInt = Field(4, ge=1, le=16)
        future_mode: Literal["basic", "extended", "new-choice"] = "basic"
        future_alias: StrictInt = Field(2, alias="future-alias", ge=1)

    class ExtendedMedia(Media):
        input_validation: Literal["content_type", "decode", "both", "future-validator"] = "content_type"

    class ExtendedAppConfig(AppConfig):
        network: ExtendedNetwork = Field(default_factory=ExtendedNetwork)
        media: ExtendedMedia = Field(default_factory=ExtendedMedia)

    # Match the schema/defaults initialized by a fresh import of a changed model.
    monkeypatch.setattr(layers, "AppConfig", ExtendedAppConfig)
    monkeypatch.setattr(layers, "DEFAULT_CONFIG", ExtendedAppConfig())
    monkeypatch.setattr(layers, "_APP_CONFIG_SHAPE", layers._model_shape(ExtendedAppConfig))


def test_added_config_fields_choices_and_aliases_survive_layer_sanitizing(extended_config, tmp_path):
    path = tmp_path / "app.yaml"
    content = "network:\n  future_capacity: 12\n  future_mode: new-choice\n  future-alias: 6\n"
    path.write_text(content, encoding="utf-8")
    raw, layer = layers._read_layer(path, role="app", config_root=tmp_path, required=True)
    effective = layers.validate_config(raw)
    assert layer.status == "applied"
    assert effective.network.future_capacity == 12
    assert effective.network.future_mode == "new-choice"
    assert effective.network.future_alias == 6
    assert path.read_text(encoding="utf-8") == content


def test_existing_config_choice_can_be_extended_in_the_schema(extended_config):
    config = layers.validate_config({"media": {"input_validation": "future-validator"}})
    assert config.media.input_validation == "future-validator"
    # This proves schema acceptance only; adding an image validator requires
    # implementing its processing semantics separately.


@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize(
    "name,value,condition",
    [
        ("future_capacity", "0", "greater than or equal to 1"),
        ("future_capacity", "17", "less than or equal to 16"),
        ("future_capacity", "private-value", "integer"),
        ("future_mode", "private-value", "supported choice"),
        ("future-alias", "0", "greater than or equal to 1"),
    ],
)
def test_added_config_field_diagnostics_include_field_and_location(
    extended_config, tmp_path, name, value, condition, json_mode, capsys
):
    path = tmp_path / "app.yaml"
    content = f"network:\n  {name}: {value}\n  headers: {{Authorization: private-secret}}\n"
    path.write_text(content, encoding="utf-8")
    assert dispatch.main(["config", "explain", "--config", str(path), *(["--json"] if json_mode else [])]) == 2
    output = capsys.readouterr()
    text = output.out + output.err
    assert f"network.{name}" in text and condition in text
    assert "private-value" not in text and "private-secret" not in text and str(tmp_path) not in text
    if json_mode:
        payload = json.loads(output.out)["error"]
        assert output.err == ""
        assert payload["code"] == "configuration_error"
        details = payload["details"]
        assert details["kind"] == "configuration_value"
        assert details["field"] == f"network.{name}"
        assert details["source"] == "app:app.yaml" and details["line"] == 2
        assert details["column"] >= 1
    assert path.read_text(encoding="utf-8") == content


@pytest.mark.parametrize("key", ["private-key", "network", "request_concurrency"])
def test_mapping_keys_never_become_schema_fields(extended_config, key):
    with pytest.raises(ConfigurationError) as caught:
        layers.validate_config({"network": {"headers": {key: 123}}})
    diagnostic = diagnostic_for(caught.value)
    assert diagnostic is not None
    assert diagnostic.details["field"] == "network.headers"
    assert diagnostic.message == "network.headers must be a string"


def test_extending_parser_choices_alone_does_not_implement_runtime_semantics(monkeypatch):
    definitions = tuple(
        replace(spec, choices=(*spec.choices, "future-level")) if spec.name == "inspection-data" else spec
        for spec in options.OPTIONS
    )
    monkeypatch.setattr(parser, "OPTIONS", definitions)
    args = parser.build_parser().parse_args(["inspect", "https://example.test/feed", "--inspection-data=future-level"])
    assert validation.validate_arguments(args) == "inspect"
    assert args.inspection_data == "future-level"
    # No request or plugin initialization is needed to establish the boundary:
    # the existing renderer still requires implementing each supported level.
    with pytest.raises(ValueError, match="inspection data level"):
        inspect._inspection_json(None, args.inspection_data)


def test_extending_parser_choices_alone_does_not_extend_configuration_schema(monkeypatch):
    definitions = tuple(
        replace(spec, choices=(*spec.choices, "FUTURE")) if spec.name == "image-format" else spec
        for spec in options.OPTIONS
    )
    monkeypatch.setattr(parser, "OPTIONS", definitions)
    args = parser.build_parser().parse_args(["doctor", "--image-format=FUTURE"])
    assert validation.validate_arguments(args) == "doctor"
    with pytest.raises(ValidationError):
        AppConfig.model_validate({"output": {"image_format": args.image_format}})
