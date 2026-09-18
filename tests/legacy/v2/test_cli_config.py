import pytest

from image_downloader.cli import build_parser
from image_downloader.config import console_logging_enabled, deep_merge, validate_config
from image_downloader.exceptions import ConfigurationError


def test_console_enabled_by_default_and_cli_can_disable() -> None:
    assert console_logging_enabled({}) is True
    assert console_logging_enabled({}, cli_disabled=True) is False
    assert console_logging_enabled({"logging": {"console": {"enabled": False}}}) is False


def test_deep_merge_replaces_lists() -> None:
    result = deep_merge(
        {"logging": {"console": {"enabled": True}}, "x": [1]},
        {"x": [2]},
    )
    assert result == {"logging": {"console": {"enabled": True}}, "x": [2]}


def test_log_path_is_not_a_cli_option() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["https://example.test", "--log-path", "other.log"])


def test_safe_log_parameters_reject_credential_like_names() -> None:
    config = validate_config({"logging": {"safe_query_parameters": ["edition"]}})
    assert config["logging"]["safe_query_parameters"] == ["edition"]
    with pytest.raises(ConfigurationError):
        validate_config({"logging": {"safe_fragment_parameters": ["access-token"]}})


@pytest.mark.parametrize("name", ["sig", "session_id", "refresh_token", "private_key", "passwd", "bearer"])
def test_safe_log_parameters_reject_new_sensitive_names(name: str) -> None:
    with pytest.raises(ConfigurationError):
        validate_config({"logging": {"safe_query_parameters": [name]}})
