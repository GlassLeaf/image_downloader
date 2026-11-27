from __future__ import annotations

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
