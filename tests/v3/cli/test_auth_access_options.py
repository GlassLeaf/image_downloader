from __future__ import annotations

import json

import pytest

from image_downloader import (
    DownloadRequest,
    InspectRequest,
    UpdateCheckRequest,
    WorkflowPlanRequest,
    WorkflowRequest,
)
from image_downloader.commands.parser import build_parser
from image_downloader.commands.validation import validate_arguments
from image_downloader.exceptions import ArgumentError
from image_downloader.immutable import thaw_json


@pytest.mark.parametrize("command", ["download", "inspect", "workflow"])
def test_auth_access_option_is_a_separate_immutable_user_override(command):
    prepared = validate_arguments(
        build_parser().parse_args(
            [
                command,
                "https://example.test/work",
                "--plugin-auth-access",
                'com.example.gallery={"mode":"anonymous"}',
            ]
        )
    )
    assert prepared.auth_access == {"com.example.gallery": {"mode": "anonymous"}}
    assert prepared.plugin_config == {}
    with pytest.raises(TypeError):
        prepared.auth_access["com.example.gallery"]["mode"] = "standard"


@pytest.mark.parametrize("value", ["no-equals", "com.example.gallery=[]", "com.example.gallery={bad", "={}"])
def test_auth_access_option_rejects_invalid_input_before_execution(value):
    with pytest.raises(ArgumentError):
        validate_arguments(
            build_parser().parse_args(
                [
                    "download",
                    "https://example.test/work",
                    "--plugin-auth-access",
                    value,
                ]
            )
        )


@pytest.mark.parametrize(
    "request_type", [DownloadRequest, InspectRequest, UpdateCheckRequest, WorkflowPlanRequest, WorkflowRequest]
)
def test_public_requests_detach_auth_access_overrides(request_type):
    values = {"com.example.gallery": {"mode": "anonymous"}}
    request = request_type(url="https://example.test/work", plugin_auth_access_overrides=values)
    values["com.example.gallery"]["mode"] = "standard"
    assert request.plugin_auth_access_overrides["com.example.gallery"]["mode"] == "anonymous"


def test_replacement_files_precede_inline_and_same_binding_is_atomic(tmp_path):
    plugin = "com.example.gallery"
    file = tmp_path / "bindings.json"
    file.write_text(
        json.dumps(
            {
                "plugin_id": plugin,
                "bindings": {
                    "x": {
                        "origins": ["https://example.com"],
                        "credentials": {"password": "PRIVATE_REF"},
                        "credential_access": "allow",
                        "login": "allow",
                    },
                    "retained": None,
                },
            }
        ),
        encoding="utf-8",
    )
    replacement = {"x": {"origins": ["https://other.com"], "credential_access": "deny", "login": "deny"}}
    prepared = validate_arguments(
        build_parser().parse_args(
            [
                "inspect",
                "https://other.com",
                "--plugin-auth-access-replace",
                plugin + "=" + json.dumps(replacement),
                "--plugin-auth-access-replace-file",
                str(file),
                "--plugin-auth-access-patch",
                plugin + '={"session":{"persistence":"ephemeral"}}',
            ]
        )
    )
    assert thaw_json(prepared.auth_replacements[plugin]) == {**replacement, "retained": None}
    assert prepared.auth_patches[plugin]["session"]["persistence"] == "ephemeral"


@pytest.mark.parametrize(
    "request_type", [DownloadRequest, InspectRequest, UpdateCheckRequest, WorkflowPlanRequest, WorkflowRequest]
)
def test_new_authentication_changes_are_detached_and_conflicts_rejected(request_type):
    changes = {"com.example.gallery": {"x": None}}
    patch = {"com.example.gallery": {"login": "deny"}}
    request = request_type(
        url="https://example.com", plugin_auth_access_replacements=changes, plugin_auth_access_patches=patch
    )
    changes["com.example.gallery"]["x"] = {}
    patch["com.example.gallery"]["login"] = "allow"
    assert request.plugin_auth_access_replacements["com.example.gallery"]["x"] is None
    assert request.plugin_auth_access_patches["com.example.gallery"]["login"] == "deny"
    with pytest.raises(TypeError):
        request.plugin_auth_access_patches["com.example.gallery"]["login"] = "allow"
