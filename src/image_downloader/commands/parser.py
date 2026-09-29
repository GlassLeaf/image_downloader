from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar, NoReturn

from ..exceptions import ConfigurationError


class _ConfigPath(argparse.Action):
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        value: object,
        option_string: str | None = None,
    ) -> None:
        setattr(namespace, self.dest, value)
        namespace.config_explicit = True


_COMMAND_NAMES = frozenset(("download", "inspect", "config", "plugin", "doctor", "cookie"))
_OPTIONS_WITH_VALUE = frozenset(
    (
        "--config",
        "--profile",
        "--plugin-root",
        "--data-root",
        "--selection-priority",
        "--plugin-config",
        "--plugin-config-file",
        "--plugin",
        "--force-plugin",
        "--plugin-download-policy",
        "--plugin-download-policy-file",
        "--fallback-generic",
        "--host",
        "--export-cookies",
        "--import-cookies",
        "--import-browser-cookies",
        "--existing-file",
        "--image-format",
        "--force-image-format",
        "--inspection-data",
        "--plugin-verification-override",
    )
)


def _normalize_cli_arguments(arguments: Sequence[str]) -> list[str]:
    """Insert the explicit download command for the legacy URL form."""
    normalized = list(arguments)
    index = 0
    while index < len(normalized):
        token = normalized[index]
        option = token.split("=", 1)[0]
        if option in _OPTIONS_WITH_VALUE:
            index += 1 if "=" in token else 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        if token in _COMMAND_NAMES:
            return normalized
        normalized.insert(index, "download")
        return normalized
    return normalized


class _CliArgumentParser(argparse.ArgumentParser):
    _active_json_error_mode: ClassVar[bool] = False

    def parse_args(self, args=None, namespace=None):
        source = sys.argv[1:] if args is None else args
        normalized = _normalize_cli_arguments(source)
        type(self)._active_json_error_mode = "--json" in normalized
        try:
            return super().parse_args(normalized, namespace)
        finally:
            type(self)._active_json_error_mode = False

    def error(self, message: str) -> NoReturn:
        """Route syntax errors through the normal safe CLI error boundary."""
        if getattr(type(self), "_active_json_error_mode", False):
            raise ConfigurationError(message)
        super().error(message)


def _add_configuration_options(
    parser: argparse.ArgumentParser,
    *,
    suppress_defaults: bool,
) -> None:
    value_default = argparse.SUPPRESS if suppress_defaults else None
    flag_default = argparse.SUPPRESS if suppress_defaults else False
    parser.add_argument("--config", type=Path, action=_ConfigPath, default=value_default)
    parser.add_argument("--profile", default=value_default)
    parser.add_argument("--plugin-root", type=Path, default=value_default)
    parser.add_argument("--data-root", type=Path, default=value_default)
    parser.add_argument("--yes", action="store_true", default=flag_default)
    parser.add_argument(
        "--plugin-verification-override",
        choices=("bypass-all", "bypass-catalog", "bypass-signature"),
        default=value_default,
        metavar="MODE",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        default=flag_default,
    )


def _add_plugin_override_options(
    parser: argparse.ArgumentParser,
    *,
    suppress_defaults: bool,
) -> None:
    repeat_default: object = argparse.SUPPRESS if suppress_defaults else []
    fallback_default: object = argparse.SUPPRESS if suppress_defaults else "auto"
    parser.add_argument(
        "--plugin-config",
        action="append",
        metavar="ID=JSON",
        default=repeat_default,
    )
    parser.add_argument(
        "--plugin-config-file",
        action="append",
        type=Path,
        metavar="ABSOLUTE_PATH",
        default=repeat_default,
    )
    parser.add_argument(
        "--fallback-generic",
        choices=("auto", "enabled", "disabled"),
        default=fallback_default,
    )


def _add_download_options(
    parser: argparse.ArgumentParser,
    *,
    suppress_defaults: bool,
    include_inspect_alias: bool = False,
) -> None:
    value_default = argparse.SUPPRESS if suppress_defaults else None
    flag_default = argparse.SUPPRESS if suppress_defaults else False
    parser.add_argument("--no-console-log", action="store_true", default=flag_default)
    mode = parser.add_mutually_exclusive_group() if include_inspect_alias else parser
    mode.add_argument(
        "--list-updated-urls",
        action="store_true",
        default=flag_default,
    )
    if include_inspect_alias:
        mode.add_argument(
            "--inspect-only",
            action="store_true",
            default=flag_default,
        )
    parser.add_argument(
        "--existing-file",
        choices=("overwrite", "skip", "rename", "error"),
        default=value_default,
    )
    image_format = parser.add_mutually_exclusive_group()
    image_format.add_argument(
        "--image-format",
        choices=("ORIGINAL", "JPEG", "PNG", "WEBP"),
        default=value_default,
    )
    image_format.add_argument(
        "--force-image-format",
        choices=("ORIGINAL", "JPEG", "PNG", "WEBP"),
        default=value_default,
    )


def _add_plugin_selection_options(
    parser: argparse.ArgumentParser,
    *,
    suppress_defaults: bool,
) -> None:
    value_default: object = argparse.SUPPRESS if suppress_defaults else None
    repeat_default: object = argparse.SUPPRESS if suppress_defaults else []
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--plugin", dest="plugin_id", metavar="ID", default=value_default)
    selection.add_argument("--force-plugin", dest="force_plugin_id", metavar="ID", default=value_default)
    parser.add_argument(
        "--plugin-download-policy",
        action="append",
        metavar="ID=JSON",
        default=repeat_default,
    )
    parser.add_argument(
        "--plugin-download-policy-file",
        action="append",
        type=Path,
        metavar="ABSOLUTE_PATH",
        default=repeat_default,
    )


def _add_inspection_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--inspection-data",
        choices=("url", "http", "all"),
        default=argparse.SUPPRESS,
        metavar="LEVEL",
    )


def _add_legacy_options(parser: argparse.ArgumentParser) -> None:
    _add_configuration_options(parser, suppress_defaults=False)
    _add_plugin_override_options(parser, suppress_defaults=False)
    _add_download_options(parser, suppress_defaults=False, include_inspect_alias=True)
    _add_plugin_selection_options(parser, suppress_defaults=False)
    _add_inspection_options(parser)
    parser.add_argument("--manifest-only", action="store_true", default=False)
    parser.add_argument(
        "--selection-priority",
        type=int,
        default=0,
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--host", help=argparse.SUPPRESS)
    parser.add_argument("--export-cookies", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--import-cookies", type=Path, help=argparse.SUPPRESS)
    parser.add_argument(
        "--import-browser-cookies",
        metavar="DOMAIN",
        help=argparse.SUPPRESS,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = _CliArgumentParser(description="local plugin API v3 image downloader")
    parser.set_defaults(config_explicit=False, command=None)
    _add_legacy_options(parser)
    commands = parser.add_subparsers(dest="command")

    download_parser = commands.add_parser(
        "download",
        help="download a URL or check for updates",
    )
    download_parser.add_argument("url")
    _add_configuration_options(download_parser, suppress_defaults=True)
    _add_plugin_override_options(download_parser, suppress_defaults=True)
    _add_download_options(download_parser, suppress_defaults=True, include_inspect_alias=True)
    _add_plugin_selection_options(download_parser, suppress_defaults=True)
    _add_inspection_options(download_parser)
    download_parser.add_argument("--manifest-only", action="store_true", default=argparse.SUPPRESS)
    download_parser.set_defaults(command_handler="download")

    inspect_parser = commands.add_parser(
        "inspect",
        help="inspect a manifest and resolved image requests without fetching image bodies",
    )
    inspect_parser.add_argument("url")
    _add_configuration_options(inspect_parser, suppress_defaults=True)
    _add_plugin_override_options(inspect_parser, suppress_defaults=True)
    _add_plugin_selection_options(inspect_parser, suppress_defaults=True)
    _add_inspection_options(inspect_parser)
    inspect_parser.add_argument("--manifest-only", action="store_true", default=argparse.SUPPRESS)
    inspect_parser.set_defaults(command_handler="inspect")

    doctor_parser = commands.add_parser(
        "doctor",
        help="validate configuration and plugins",
    )
    _add_configuration_options(doctor_parser, suppress_defaults=True)
    _add_plugin_override_options(doctor_parser, suppress_defaults=True)
    _add_download_options(doctor_parser, suppress_defaults=True)
    doctor_parser.add_argument("--host", default=argparse.SUPPRESS)
    doctor_parser.set_defaults(command_handler="doctor")

    config_parser = commands.add_parser(
        "config",
        help="locate, explain, or initialize application configuration",
    )
    config_parser.add_argument("command_args", nargs="*")
    _add_configuration_options(config_parser, suppress_defaults=True)
    _add_download_options(config_parser, suppress_defaults=True)
    config_parser.add_argument("--host", default=argparse.SUPPRESS)
    config_parser.set_defaults(command_handler="config")

    plugin_parser = commands.add_parser(
        "plugin",
        help="install, trust, revoke, uninstall, or list plugins",
    )
    plugin_parser.add_argument("command_args", nargs="*")
    _add_configuration_options(plugin_parser, suppress_defaults=True)
    plugin_parser.add_argument(
        "--selection-priority",
        type=int,
        default=argparse.SUPPRESS,
    )
    plugin_parser.set_defaults(command_handler="plugin")

    cookie_parser = commands.add_parser(
        "cookie",
        help="export or import the profile Cookie store",
    )
    cookie_parser.add_argument(
        "cookie_action",
        choices=("export", "import", "browser-import"),
    )
    cookie_parser.add_argument("cookie_value")
    _add_configuration_options(cookie_parser, suppress_defaults=True)
    cookie_parser.set_defaults(command_handler="cookie")
    return parser
