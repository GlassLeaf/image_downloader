"""Shared CLI command protocol and option validation."""

from __future__ import annotations

import argparse
import math
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from ..configuration.hosts import normalize_host
from ..configuration.models import Output, PluginDownloadPolicy
from ..diagnostics import argument_error, diagnostic_for, validation_diagnostic
from ..exceptions import ConfigurationError
from ..storage.path_safety import canonical_path, existing_directory
from .options import CONFLICT_GROUPS, LEGACY_COOKIE_OPTIONS, OPTIONS
from .parser import is_http_url


class CommandHandler(Protocol):
    async def handle(self, args: argparse.Namespace) -> int: ...


_OPTION_DEFAULTS: Mapping[str, object] = {
    option.attribute: [] if option.repeat else None if option.default == argparse.SUPPRESS else option.default
    for option in OPTIONS
}


def _reject_command_options(args: argparse.Namespace, names: Sequence[str], command: str) -> None:
    for name in names:
        if name not in _OPTION_DEFAULTS:
            continue
        default = _OPTION_DEFAULTS[name]
        if getattr(args, name, default) != default:
            option = name.replace("_", "-")
            raise argument_error(
                f"--{option} is not valid for the {command} command",
                "option_scope",
                option=f"--{option}",
                command=command,
            )


_OPTION_NAMES = tuple(option.name for option in OPTIONS)
_OPTIONS_BY_NAME = {option.name: option for option in OPTIONS}
_LEGACY_COOKIE = LEGACY_COOKIE_OPTIONS


def _command_path(args: argparse.Namespace) -> str:
    command = getattr(args, "command_handler", None)
    if command is None:
        return (
            "cookie"
            if any(getattr(args, name.replace("-", "_"), None) is not None for name in _LEGACY_COOKIE)
            else "cli"
        )
    words: Sequence[str] = getattr(args, "command_args", [])
    if command == "verify":
        return "verify " + str(args.verify_resource)
    if command in {"config", "plugin"}:
        return " ".join([command, *words[: 2 if words and words[0] == "profile" else 1]])
    if command == "state":
        return "state workflow " + (words[1] if len(words) > 1 else "list")
    return str(command)


def _allowed_options(args: argparse.Namespace, command: str) -> set[str]:
    scope = "download --inspect-only" if command == "download" and getattr(args, "inspect_only", False) else command
    return {option.name for option in OPTIONS if scope in option.commands}


def _explicit_options(args: argparse.Namespace) -> set[str]:
    explicit = getattr(args, "_explicit_options", None)
    if explicit is not None:
        return {name.removeprefix("--") for name in explicit}
    # Public run() still accepts manually constructed legacy Namespaces.
    return {
        option.name
        for option in OPTIONS
        if getattr(args, option.attribute, _OPTION_DEFAULTS[option.attribute]) != _OPTION_DEFAULTS[option.attribute]
    }


def _check_conflicts(args: argparse.Namespace, explicit: set[str], *, syntax_only: bool = False) -> None:
    groups = list(CONFLICT_GROUPS)
    if getattr(args, "fallback_generic", "auto") != "auto":
        groups.extend([("plugin", "fallback-generic"), ("force-plugin", "fallback-generic")])
    if syntax_only:
        groups = groups[:3]
    for group in groups:
        selected = ["--" + name for name in group if name in explicit]
        if len(selected) > 1:
            raise argument_error("cannot combine " + " and ".join(selected), "option_conflict", options=selected)
    selected = ["--" + name for name in _OPTION_NAMES if name in explicit & _LEGACY_COOKIE]
    if selected and getattr(args, "command_handler", None) is not None and not syntax_only:
        raise argument_error(
            "legacy Cookie options cannot be combined with a command",
            "option_conflict",
            options=selected,
            command=str(args.command_handler),
        )


def _check_paths(args: argparse.Namespace, command: str) -> None:
    ignored_output = not _option_has_effect("output-dir", args, command)
    for option in ("config", "data_root", "plugin_root", "output_dir"):
        value = getattr(args, option, None)
        if value is None or (option == "output_dir" and (ignored_output or command == "verify workflow")):
            continue
        name = "--" + option.replace("_", "-")
        try:
            if option == "config":
                canonical_path(value, name)
            else:
                existing_directory(value, name)
        except ConfigurationError as exc:
            raise argument_error(
                f"{name} requires an absolute safe {'file' if option == 'config' else 'directory'} path",
                "invalid_value",
                option=name,
            ) from exc
    destination = getattr(args, "destination", None)
    target = getattr(args, "target", None)
    if command == "config init" and destination is not None:
        _check_positional_path(destination, command, "path", directory=False)
    if command in {"plugin install", "plugin trust"}:
        assert target is not None
        _check_positional_path(target, command, "source", directory=True)


def _check_positional_path(value: str, command: str, argument: str, *, directory: bool) -> None:
    try:
        if directory:
            existing_directory(Path(value), argument)
        else:
            canonical_path(Path(value), argument)
    except ConfigurationError as exc:
        raise argument_error(
            f"{command} requires an absolute safe {argument} path", "invalid_value", argument=argument, command=command
        ) from exc


def _check_empty_values(args: argparse.Namespace, command: str) -> None:
    for name in ("plugin", "force-plugin", "import-browser-cookies", "export-cookies", "import-cookies"):
        option = _OPTIONS_BY_NAME.get(name)
        if option is None:
            continue
        value = getattr(args, option.attribute, None)
        if value is not None and (value == "" or "--" + name in getattr(args, "_empty_options", ())):
            raise argument_error(f"--{name} requires a non-empty value", "invalid_value", option="--" + name)
    if command == "cookie" and getattr(args, "cookie_action", None) is not None and not args.cookie_value:
        raise argument_error("Cookie action requires a non-empty value", "invalid_value", argument="cookie_value")
    if command in {"plugin revoke", "plugin uninstall"} and not getattr(args, "target", None):
        raise argument_error(f"{command} requires a non-empty ID", "invalid_value", command=command, argument="target")


def _check_verification_values(args: argparse.Namespace, command: str) -> None:
    if command == "verify logs":
        _check_verification_path(Path(args.target), "path")
    if command == "verify workflow":
        from uuid import UUID

        try:
            if str(UUID(args.target)) != args.target:
                raise ValueError
        except ValueError as exc:
            raise argument_error("RUN_ID must be a canonical UUID", "invalid_value", argument="target") from exc
        if args.output_dir is None:
            raise argument_error("verify workflow requires --output-dir", "missing_value", option="--output-dir")
        _check_verification_path(args.output_dir, "--output-dir")


def _check_verification_path(path: Path, argument: str) -> None:
    # Unsafe existing nodes are findings (exit 5), rather than syntax errors (exit 2).
    if not path.is_absolute() or ".." in path.parts:
        raise argument_error("verification requires an absolute path without ..", "invalid_value", argument=argument)


def _check_values(args: argparse.Namespace, command: str) -> None:
    _check_verification_values(args, command)
    if hasattr(args, "url") and not is_http_url(args.url):
        raise argument_error("URL must be an absolute HTTP(S) URL with a host", "invalid_value", argument="url")
    _check_empty_values(args, command)
    for name, positive in (
        ("limit", True),
        ("workflow_retries", False),
        ("workflow_retry_delay", False),
        ("workflow_retry_timeout", True),
    ):
        value = getattr(args, name, None)
        if value is not None and (not math.isfinite(value) or (value <= 0 if positive else value < 0)):
            option = "--" + name.replace("_", "-")
            condition = "positive" if positive else "non-negative"
            raise argument_error(f"{option} must be {condition} and finite", "invalid_value", option=option)
    for name in ("profile", "profile_name"):
        value = getattr(args, name, None)
        if value is not None and not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            raise argument_error(
                "profile name must contain only letters, numbers, '_' or '-'",
                "invalid_value",
                **({"argument": "profile_name"} if name == "profile_name" else {"option": "--profile"}),
            )
    host = getattr(args, "host", None)
    if host is not None:
        try:
            if "://" in host:
                if not is_http_url(host):
                    raise ValueError
            else:
                normalize_host(host)
        except (ConfigurationError, ValueError) as exc:
            raise argument_error(
                "--host requires a bare host or absolute HTTP(S) URL", "invalid_value", option="--host"
            ) from exc
    _check_paths(args, command)
    if getattr(args, "directory_format", None) is not None and _option_has_effect("directory-format", args, command):
        try:
            Output(directory_format=args.directory_format)
        except ValidationError as exc:
            raise argument_error(
                "--directory-format contains unsupported or image-only format tokens",
                "invalid_value",
                option="--directory-format",
            ) from exc
    from .setup import _download_policy_overrides, _runtime_overrides

    plugin_config = _runtime_overrides(args)
    policies = _download_policy_overrides(args)
    for policy in policies.values():
        try:
            PluginDownloadPolicy.model_validate(policy)
        except ValidationError as exc:
            diagnostic = diagnostic_for(validation_diagnostic(exc.errors()[0], model=PluginDownloadPolicy))
            assert diagnostic is not None
            options = [
                "--" + name
                for name in _OPTION_NAMES
                if name in {"plugin-download-policy", "plugin-download-policy-file"}
                and getattr(args, name.replace("-", "_"), [])
            ]
            raise argument_error(
                "plugin download policy: " + diagnostic.message,
                "invalid_value",
                options=options,
                field=diagnostic.details["field"],
            ) from exc
    args._prepared_plugin_config = plugin_config
    args._prepared_download_policy = policies


def _option_has_effect(name: str, args: argparse.Namespace, command: str) -> bool:
    option = _OPTIONS_BY_NAME.get(name)
    if option is None:
        return False
    scope = command
    if command == "download":
        if getattr(args, "inspect_only", False):
            scope = "download --inspect-only"
        elif getattr(args, "list_updated_urls", False):
            scope = "download --list-updated-urls"
    return scope not in option.no_effect


def validate_arguments(args: argparse.Namespace) -> str:
    """Validate before configuration resolution or any execution side effect."""
    for name in ("_prepared_plugin_config", "_prepared_download_policy"):
        vars(args).pop(name, None)
    command = _command_path(args)
    if command == "cli":
        raise argument_error("a command or absolute HTTP(S) URL is required", "missing_command", argument="command")
    explicit = _explicit_options(args)
    allowed = _allowed_options(args, command)
    for name in _OPTION_NAMES:
        if name in explicit and name not in allowed:
            option = "--" + name
            displayed_command = (
                "download --inspect-only (inspect)"
                if command == "download" and getattr(args, "inspect_only", False)
                else command
            )
            raise argument_error(
                f"{option} is not valid for {displayed_command}",
                "option_scope",
                option=option,
                command=displayed_command,
            )
    _check_conflicts(args, explicit)
    value_errors = getattr(args, "_value_errors", ())
    if value_errors:

        def priority(error: BaseException) -> int:
            diagnostic = diagnostic_for(error)
            option = str(diagnostic.details.get("option", "")) if diagnostic is not None else ""
            return _OPTION_NAMES.index(option.removeprefix("--"))

        raise min(value_errors, key=priority)
    _check_values(args, command)
    return command.split(" ", 1)[0]
