"""Keep declared CLI choices in sync with their configuration/runtime consumers."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import get_args, get_type_hints

import pytest
import yaml

from image_downloader.commands import inspect, setup
from image_downloader.commands.options import OPTIONS
from image_downloader.commands.parser import build_parser
from image_downloader.configuration.models import AppConfig, Output
from image_downloader.models import DownloadManifest, ManifestInspectionResult, WorkflowPlanResult
from image_downloader.plugins.plugin_manifest import PluginVerificationOverride


def choices(name):
    return next((spec.choices for spec in OPTIONS if spec.name == name), ())


@pytest.mark.parametrize(
    "option,field,value",
    [
        (option, field, value)
        for option, field in (
            ("image-format", "image_format"),
            ("force-image-format", "image_format"),
            ("existing-file", "existing_file"),
        )
        for value in choices(option)
    ],
)
def test_every_declared_output_choice_is_accepted_by_configuration(option, field, value):
    args = build_parser().parse_args(["download", "https://example.test/feed", f"--{option}={value}"])
    if option == "force-image-format":
        output = Output.model_validate({field: args.force_image_format})
    else:
        output = AppConfig.model_validate(setup._app_override(args)).output
    assert getattr(output, field) == value


@pytest.mark.parametrize("level", choices("inspection-data"))
def test_every_declared_inspection_choice_has_a_renderer(level):
    result = ManifestInspectionResult(
        "https://example.test/feed", "com.example.test", DownloadManifest("Book", ()), False
    )
    output = inspect._inspection_json(result, level)
    assert output["inspection_data"] == level
    assert output["request_resolution"] == "manifest_only"


@pytest.mark.parametrize("mode", choices("fallback-generic"))
def test_every_declared_fallback_choice_has_explicit_semantics(mode):
    expected = {"auto": None, "enabled": True, "disabled": False}
    assert mode in expected, "a new choice requires defined fallback semantics"
    assert setup._fallback(argparse.Namespace(fallback_generic=mode)) is expected[mode]


def test_verification_choices_match_runtime_contract():
    assert set(choices("plugin-verification-override")) <= set(get_args(PluginVerificationOverride))


def test_workflow_scope_choices_match_runtime_result_contract():
    assert set(choices("download-scope")) <= set(get_args(get_type_hints(WorkflowPlanResult)["download_scope"]))


@pytest.mark.parametrize(
    "spec", [spec for spec in OPTIONS if spec.choices is not None and spec.default not in (None, argparse.SUPPRESS)]
)
def test_declared_option_default_is_still_a_supported_choice(spec):
    assert spec.default in spec.choices, "a removed/renamed choice must also be updated in defaults"


def test_configuration_defaults_satisfy_current_schema():
    AppConfig.model_validate(AppConfig().model_dump(by_alias=True, warnings=False))


def test_bundled_configuration_satisfies_current_schema():
    from image_downloader import configuration

    path = Path(configuration.__file__).resolve().parents[1] / "app.yaml"
    AppConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
