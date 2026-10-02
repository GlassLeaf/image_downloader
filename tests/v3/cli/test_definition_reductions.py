"""Simulate removed/changed definitions without removing shipped CLI features."""

from __future__ import annotations

import argparse
import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from typing import Literal

import pytest
from pydantic import Field, StrictStr, ValidationError, create_model

from image_downloader.commands import dispatch, options, parser, setup, validation
from image_downloader.configuration import layers
from image_downloader.configuration.models import AppConfig, Media, Network, StrictModel
from image_downloader.diagnostics import diagnostic_for
from image_downloader.exceptions import ConfigurationError

URL = "https://example.test/feed"


@pytest.fixture
def change_definitions(monkeypatch):
    def apply(definitions):
        # Reproduce the bindings calculated on a fresh import after a source edit.
        monkeypatch.setattr(options, "OPTIONS", definitions)
        monkeypatch.setattr(parser, "OPTIONS", definitions)
        monkeypatch.setattr(validation, "OPTIONS", definitions)
        monkeypatch.setattr(validation, "_OPTION_NAMES", tuple(spec.name for spec in definitions))
        monkeypatch.setattr(validation, "_OPTIONS_BY_NAME", {spec.name: spec for spec in definitions})
        monkeypatch.setattr(
            validation,
            "_OPTION_DEFAULTS",
            {
                spec.attribute: [] if spec.repeat else None if spec.default == argparse.SUPPRESS else spec.default
                for spec in definitions
            },
        )

    def forbidden(*args, **kwargs):
        pytest.fail("removed/invalid arguments or help must not resolve configuration or execute")

    class ForbiddenHandler:
        async def handle(self, args):
            forbidden()

    monkeypatch.setattr(setup, "_resolved_config_for", forbidden)
    for name in dispatch._COMMAND_HANDLERS:
        monkeypatch.setitem(dispatch._COMMAND_HANDLERS, name, ForbiddenHandler())
    return apply


@pytest.mark.parametrize("removed", [spec.name for spec in options.OPTIONS])
def test_removing_any_option_does_not_break_unrelated_common_validation(change_definitions, removed, monkeypatch):
    removed_spec = next(spec for spec in options.OPTIONS if spec.name == removed)
    definitions = tuple(spec for spec in options.OPTIONS if spec.name != removed)
    change_definitions(definitions)
    seen = []

    class Boundary:
        async def handle(self, args):
            seen.append(args.command_handler)
            return 31

    monkeypatch.setitem(dispatch._COMMAND_HANDLERS, "doctor", Boundary())
    args = parser.build_parser().parse_args(["doctor"])
    assert not hasattr(args, removed_spec.attribute)
    validation._reject_command_options(args, (removed_spec.attribute,), "doctor")
    assert asyncio.run(dispatch.run(args)) == 31
    assert seen == ["doctor"]


@pytest.mark.parametrize("removed", [spec.name for spec in options.OPTIONS])
@pytest.mark.parametrize("position", [0, 1, 2])
def test_removed_option_is_unknown_and_absent_from_help(change_definitions, removed, position, capsys):
    definitions = tuple(spec for spec in options.OPTIONS if spec.name != removed)
    change_definitions(definitions)
    command = ["config", "path"]
    assert dispatch.main([*command[:position], "--" + removed, *command[position:]]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == f"error [argument_error]: unrecognized option: --{removed}\n"
    built = parser.build_parser()
    assert "--" + removed not in built._option_string_actions
    assert "--" + removed not in built.format_help().split()


@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize("position", [0, 1, 2])
@pytest.mark.parametrize(
    "name,command,old,kept",
    [
        ("image-format", ["doctor"], "WEBP", ("ORIGINAL", "JPEG", "PNG")),
        ("inspection-data", ["inspect", URL], "http", ("url", "all")),
        ("fallback-generic", ["doctor"], "enabled", ("auto", "disabled")),
    ],
)
def test_removed_choices_are_rejected_with_only_current_choices(
    change_definitions, name, command, old, kept, position, json_mode, capsys
):
    definitions = tuple(replace(spec, choices=kept) if spec.name == name else spec for spec in options.OPTIONS)
    change_definitions(definitions)
    position = min(position, len(command))
    words = [*command[:position], f"--{name}={old}", *command[position:], *(["--json"] if json_mode else [])]
    assert dispatch.main(words) == 2
    output = capsys.readouterr()
    if json_mode:
        payload = json.loads(output.out)["error"]
        assert output.err == ""
        assert payload["code"] == "argument_error"
        assert payload["details"] == {"kind": "invalid_value", "option": "--" + name, "choices": list(kept)}
        assert old not in payload["message"]
    else:
        assert output.out == ""
        assert output.err == f"error [argument_error]: invalid value for --{name}; choose from: {', '.join(kept)}\n"


def test_remaining_choices_still_pass_validation(change_definitions):
    definitions = tuple(
        replace(spec, choices=("url", "all")) if spec.name == "inspection-data" else spec for spec in options.OPTIONS
    )
    change_definitions(definitions)
    for level in ("url", "all"):
        args = parser.build_parser().parse_args(["inspect", URL, f"--inspection-data={level}"])
        assert validation.validate_arguments(args) == "inspect"
        assert args.inspection_data == level


def test_removed_conflict_peer_does_not_reject_surviving_option(change_definitions):
    change_definitions(tuple(spec for spec in options.OPTIONS if spec.name != "force-plugin"))
    args = parser.build_parser().parse_args(["download", URL, "--plugin=com.example.test"])
    assert validation.validate_arguments(args) == "download"


def test_renamed_option_uses_new_name_in_scope_errors(change_definitions, capsys):
    definitions = tuple(
        replace(spec, name="destination-root") if spec.name == "output-dir" else spec for spec in options.OPTIONS
    )
    change_definitions(definitions)
    assert dispatch.main(["doctor", "--destination-root=private-value", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)["error"]
    assert payload["details"] == {"kind": "option_scope", "option": "--destination-root", "command": "doctor"}
    assert dispatch.main(["doctor", "--output-dir=private-value", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["details"]["kind"] == "unknown_option"


def test_renamed_json_option_controls_early_error_output(change_definitions, capsys):
    definitions = tuple(replace(spec, name="machine") if spec.name == "json" else spec for spec in options.OPTIONS)
    change_definitions(definitions)
    assert dispatch.main(["--machine", "--unknown-option=private-value"]) == 2
    output = capsys.readouterr()
    assert output.err == ""
    payload = json.loads(output.out)["error"]
    assert payload["details"] == {"kind": "unknown_option", "option": "--unknown-option"}
    assert "private-value" not in output.out


def test_narrowed_scope_and_changed_default_are_used(change_definitions, capsys):
    definitions = tuple(
        replace(spec, commands=frozenset({"plugin trust"}), default=7) if spec.name == "selection-priority" else spec
        for spec in options.OPTIONS
    )
    change_definitions(definitions)
    args = parser.build_parser().parse_args(["plugin", "trust", str(__file__)])
    assert args.selection_priority == 7
    assert dispatch.main(["plugin", "install", str(__file__), "--selection-priority=7", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)["error"]
    assert payload["details"]["kind"] == "option_scope"


def test_changed_option_type_is_enforced(change_definitions, capsys):
    definitions = tuple(replace(spec, converter=int) if spec.name == "host" else spec for spec in options.OPTIONS)
    change_definitions(definitions)
    with pytest.raises(SystemExit) as stopped:
        parser.build_parser().parse_args(["doctor", "--host=previous-host.test"])
    assert stopped.value.code == 2
    assert "previous-host.test" not in capsys.readouterr().err
    assert dispatch.main(["doctor", "--host=previous-host.test", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)["error"]
    assert "integer" in payload["message"] and payload["details"]["kind"] == "invalid_value"


@pytest.fixture
def reduced_config(monkeypatch):
    class NarrowedMedia(Media):
        input_validation: Literal["content_type", "decode"] = "decode"
        content_type_mismatch: Literal["accept", "reject"] = "accept"

    class NarrowedConfig(AppConfig):
        media: NarrowedMedia = Field(default_factory=NarrowedMedia)

    monkeypatch.setattr(layers, "AppConfig", NarrowedConfig)
    monkeypatch.setattr(layers, "DEFAULT_CONFIG", NarrowedConfig())
    monkeypatch.setattr(layers, "_APP_CONFIG_SHAPE", layers._model_shape(NarrowedConfig))


@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize("field,old", [("input_validation", "both"), ("content_type_mismatch", "error")])
def test_removed_or_renamed_configuration_choice_is_specific_and_does_not_rewrite(
    reduced_config, tmp_path, field, old, json_mode, capsys
):
    path = tmp_path / "app.yaml"
    content = f"media:\n  {field}: {old}\nnetwork:\n  headers: {{Authorization: private-secret}}\n"
    path.write_text(content, encoding="utf-8")
    assert dispatch.main(["config", "explain", "--config", str(path), *(["--json"] if json_mode else [])]) == 2
    output = capsys.readouterr()
    assert "private-secret" not in output.out + output.err and str(tmp_path) not in output.out + output.err
    if json_mode:
        payload = json.loads(output.out)["error"]
        assert payload["code"] == "configuration_error"
        assert payload["details"]["field"] == f"media.{field}"
        assert payload["details"]["line"] == 2 and payload["details"]["source"] == "app:app.yaml"
    else:
        assert f"media.{field} must use a supported choice" in output.err
    assert path.read_text(encoding="utf-8") == content


def test_changed_configuration_choices_and_defaults_take_effect(reduced_config):
    config = layers.validate_config({"media": {"content_type_mismatch": "reject"}})
    assert config.media.input_validation == "decode"
    assert config.media.content_type_mismatch == "reject"
    # Changing a model default alone does not change the higher-priority bundled
    # baseline: its content_type remains valid and overrides the new decode.
    effective = layers.resolve_application_config(None)
    assert effective.config.media.input_validation == "content_type"


def test_removed_configuration_field_follows_existing_unknown_key_cleanup(tmp_path, monkeypatch):
    reduced_media = create_model(
        "ReducedMedia",
        __base__=StrictModel,
        **{
            name: (field.annotation, deepcopy(field))
            for name, field in Media.model_fields.items()
            if name != "max_image_pixels"
        },
    )
    reduced = create_model(
        "ReducedConfig", __base__=AppConfig, media=(reduced_media, Field(default_factory=reduced_media))
    )
    monkeypatch.setattr(layers, "AppConfig", reduced)
    monkeypatch.setattr(layers, "DEFAULT_CONFIG", reduced())
    monkeypatch.setattr(layers, "_APP_CONFIG_SHAPE", layers._model_shape(reduced))
    with pytest.raises(ConfigurationError):
        layers.validate_config({"media": {"max_image_pixels": 12345}})
    path = tmp_path / "app.yaml"
    content = "media:\n  max_image_pixels: 12345\n  input_validation: decode\n"
    path.write_text(content, encoding="utf-8")
    plans = []
    raw, _ = layers._read_layer(path, role="app", config_root=tmp_path, required=True, cleanups=plans)
    assert raw == {"media": {"input_validation": "decode"}}
    assert plans[0].removals == (("media", "max_image_pixels"),)
    assert path.read_text(encoding="utf-8") == content
    layers._apply_cleanup(plans[0])
    assert "max_image_pixels" not in path.read_text(encoding="utf-8")


def test_changing_configuration_type_rejects_old_valid_values(monkeypatch):
    class ChangedMedia(Media):
        max_image_pixels: StrictStr = "unlimited"

    class ChangedConfig(AppConfig):
        media: ChangedMedia = Field(default_factory=ChangedMedia)

    monkeypatch.setattr(layers, "AppConfig", ChangedConfig)
    with pytest.raises(ConfigurationError) as caught:
        layers.validate_config({"media": {"max_image_pixels": 12345}})
    diagnostic = diagnostic_for(caught.value)
    assert diagnostic is not None and diagnostic.message == "media.max_image_pixels must be a string"


@pytest.mark.parametrize("value,condition", [(1, "greater than or equal to 3"), (7, "less than or equal to 6")])
def test_narrowed_configuration_bounds_reject_old_valid_values(monkeypatch, value, condition):
    class NarrowedNetwork(Network):
        request_concurrency: int = Field(4, ge=3, le=6)

    class NarrowedConfig(AppConfig):
        network: NarrowedNetwork = Field(default_factory=NarrowedNetwork)

    monkeypatch.setattr(layers, "AppConfig", NarrowedConfig)
    with pytest.raises(ConfigurationError) as caught:
        layers.validate_config({"network": {"request_concurrency": value}})
    diagnostic = diagnostic_for(caught.value)
    assert diagnostic is not None and diagnostic.message == f"network.request_concurrency must be {condition}"


def test_choice_renaming_alone_can_change_consumer_semantics(change_definitions):
    definitions = tuple(
        replace(spec, choices=("auto", "on", "disabled")) if spec.name == "fallback-generic" else spec
        for spec in options.OPTIONS
    )
    change_definitions(definitions)
    args = parser.build_parser().parse_args(["doctor", "--fallback-generic=on"])
    assert validation.validate_arguments(args) == "doctor"
    # Acceptance is not a migration implementation: the old consumer treats
    # everything other than auto/enabled as disabled until it is updated.
    assert setup._fallback(args) is False


def test_removing_option_requires_removing_consumer_references(change_definitions):
    change_definitions(tuple(spec for spec in options.OPTIONS if spec.name != "fallback-generic"))
    args = parser.build_parser().parse_args(["doctor"])
    assert validation.validate_arguments(args) == "doctor"
    with pytest.raises(AttributeError, match="fallback_generic"):
        setup._fallback(args)


def test_removed_choice_must_also_be_removed_from_defaults(change_definitions):
    definitions = tuple(
        replace(spec, choices=("enabled", "disabled")) if spec.name == "fallback-generic" else spec
        for spec in options.OPTIONS
    )
    change_definitions(definitions)
    args = parser.build_parser().parse_args(["doctor"])
    # argparse doesn't check an absent option's default against choices.
    assert args.fallback_generic == "auto"
    assert args.fallback_generic not in next(spec.choices for spec in definitions if spec.name == "fallback-generic")


def test_changed_model_choice_must_also_be_removed_from_defaults():
    class InvalidDefaultMedia(Media):
        input_validation: Literal["decode"] = "content_type"

    # Model defaults are not necessarily validated during construction.
    default = InvalidDefaultMedia()
    assert default.input_validation == "content_type"
    with pytest.raises(ValidationError):
        InvalidDefaultMedia.model_validate(default.model_dump())
