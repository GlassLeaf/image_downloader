"""Finite-number rejection keeps each existing validation boundary intact."""

from __future__ import annotations

import asyncio
import sys

import pytest
import yaml
from pydantic import ValidationError

from image_downloader import ArgumentError, ConfigurationError, DownloadService, WorkflowRequest
from image_downloader._numeric import _is_finite
from image_downloader.commands import dispatch
from image_downloader.commands.parser import build_parser
from image_downloader.commands.validation import validate_arguments
from image_downloader.config import AppConfig, resolve_application_config, validate_config
from image_downloader.diagnostics import diagnostic_for
from image_downloader.storage.interprocess_lock import InterProcessFileLock

FIELDS = (
    ("output", "lock_timeout_seconds", False, False),
    ("network", "request_timeout_seconds", True, False),
    ("network", "connect_timeout_seconds", True, True),
    ("network", "read_timeout_seconds", True, True),
    ("network", "write_timeout_seconds", True, True),
    ("network", "pool_timeout_seconds", True, True),
    ("network", "retry_max_delay_seconds", False, False),
    ("network", "global_request_interval_seconds", False, False),
)
BAD_VALUES = [10**400, -(10**400), float("nan"), float("inf"), -float("inf"), True, "1", -1]


@pytest.mark.parametrize("section,field,positive,optional", FIELDS)
@pytest.mark.parametrize(
    "value", BAD_VALUES, ids=["huge", "negative-huge", "nan", "inf", "-inf", "bool", "str", "negative"]
)
def test_all_finite_settings_have_safe_field_diagnostics(section, field, positive, optional, value):
    with pytest.raises(ConfigurationError) as caught:
        validate_config({section: {field: value}})
    assert type(caught.value) is ConfigurationError
    diagnostic = diagnostic_for(caught.value)
    assert diagnostic.details["field"] == section + "." + field
    assert str(10**400) not in str(caught.value)
    assert isinstance(caught.value.__cause__, ValidationError)


@pytest.mark.parametrize("section,field,positive,optional", FIELDS)
@pytest.mark.parametrize("value", [0, 1, 1.25, None, int(sys.float_info.max)])
def test_finite_settings_keep_bounds_optional_values_and_large_finite_values(section, field, positive, optional, value):
    if (value is None and not optional) or (value == 0 and positive):
        with pytest.raises(ConfigurationError):
            validate_config({section: {field: value}})
    else:
        config = validate_config({section: {field: value}})
        assert getattr(getattr(config, section), field) == value


def test_float_conversion_boundary_and_existing_lock_type_policy(tmp_path):
    boundary = int(sys.float_info.max)
    assert _is_finite(boundary) and _is_finite(-boundary)
    assert not _is_finite(2**1024) and not _is_finite(-(2**1024))
    lock = InterProcessFileLock(tmp_path / "absent" / "lock", timeout_seconds=True)
    assert lock.timeout_seconds is True
    with pytest.raises(TypeError):
        InterProcessFileLock(tmp_path / "lock", timeout_seconds="1")
    assert not (tmp_path / "absent").exists()


@pytest.mark.parametrize("layer", ["app", "profile_app", "site", "runtime"])
@pytest.mark.parametrize("value", [10**400, -(10**400)], ids=["huge", "negative-huge"])
def test_yaml_and_runtime_layers_keep_location_without_input_values(tmp_path, layer, value):
    main = tmp_path / "app.yaml"
    main.write_text("{}\n", encoding="utf-8")
    document = {"network": {"request_timeout_seconds": value}}
    paths = {
        "app": main,
        "profile_app": tmp_path / "profiles/default/app.yaml",
        "site": tmp_path / "sites/example.test.yaml",
    }
    if layer != "runtime":
        path = paths[layer]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(ConfigurationError) as caught:
        resolve_application_config(
            main, site="example.test", source="explicit", runtime_override=document if layer == "runtime" else None
        )
    diagnostic = diagnostic_for(caught.value)
    assert diagnostic.details["field"] == "network.request_timeout_seconds"
    assert layer in diagnostic.details["source"]
    assert str(abs(value)) not in str(caught.value)
    if layer != "runtime":
        assert diagnostic.details["line"] == 2
        assert str(abs(value)) in paths[layer].read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["workflow_retry_delay", "workflow_retry_timeout"])
@pytest.mark.parametrize(
    "value",
    [10**400, -(10**400), True, "1", float("nan"), float("inf")],
    ids=["huge", "negative-huge", "bool", "str", "nan", "inf"],
)
def test_workflow_direct_api_rejects_before_operation_lock(name, value):
    class Unstarted:
        @property
        def _operation_lock(self):
            pytest.fail("invalid workflow arguments must not start an operation")

    with pytest.raises(ValueError, match=name) as caught:
        asyncio.run(
            DownloadService.workflow(Unstarted(), WorkflowRequest(url="https://example.test/feed", **{name: value}))
        )
    assert type(caught.value) is ValueError


@pytest.mark.parametrize(
    "value",
    [10**400, -(10**400), float("nan"), float("inf"), -1],
    ids=["huge", "negative-huge", "nan", "inf", "negative"],
)
def test_lock_constructor_rejects_before_filesystem_access(tmp_path, value):
    path = tmp_path / "absent" / "lock"
    with pytest.raises(ValueError, match="finite and nonnegative"):
        InterProcessFileLock(path, timeout_seconds=value)
    assert not path.parent.exists()


@pytest.mark.parametrize("option", ["workflow-retries", "workflow-retry-delay", "workflow-retry-timeout", "limit"])
def test_cli_rejects_huge_values_before_dispatch(option, monkeypatch, capsys):
    words = ["state", "workflow", "history"] if option == "limit" else ["workflow", "https://example.test/feed"]
    value = str(10**400)
    args = build_parser().parse_args([*words, "--" + option, value])
    with pytest.raises(ArgumentError) as caught:
        validate_arguments(args)
    assert diagnostic_for(caught.value).details["option"] == "--" + option

    class Forbidden:
        async def handle(self, args):
            pytest.fail("invalid arguments must not enter a command handler")

    for name in dispatch._COMMAND_HANDLERS:
        monkeypatch.setitem(dispatch._COMMAND_HANDLERS, name, Forbidden())
    assert dispatch.main([*words, "--" + option, value, "--json"]) == 2
    assert value not in capsys.readouterr().out


@pytest.mark.parametrize("name", ["workflow_retry_delay", "workflow_retry_timeout"])
def test_workflow_validation_rejects_huge_values_before_command_preparation(name):
    args = build_parser().parse_args(["workflow", "https://example.test/feed"])
    setattr(args, name, 10**400)
    with pytest.raises(ArgumentError) as caught:
        validate_arguments(args)
    assert diagnostic_for(caught.value).details["option"] == "--" + name.replace("_", "-")


def test_defaults_are_unchanged():
    config = AppConfig()
    assert config.network.request_timeout_seconds == config.output.lock_timeout_seconds == 30
    assert config.network.connect_timeout_seconds is None
    assert config.network.global_request_interval_seconds == 0
