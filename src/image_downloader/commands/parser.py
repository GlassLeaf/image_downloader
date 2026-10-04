from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from typing import NoReturn
from urllib.parse import urlsplit

from ..diagnostics import argument_error, safe_option
from ..exceptions import ArgumentError
from .options import OPTIONS

_COMMAND_NAMES = frozenset(
    ("download", "workflow", "inspect", "config", "plugin", "doctor", "cookie", "state", "verify")
)


def _negative_number(value: str) -> bool:
    return bool(
        re.fullmatch(r"-(?:\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?|inf|nan)", value, re.IGNORECASE)
    )


def is_http_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme.lower() in {"http", "https"}
            and bool(parsed.hostname)
            and not any(char.isspace() or ord(char) < 32 for char in value)
            and parsed.port != 0
        )
    except ValueError:
        return False


class _CliArgumentParser(argparse.ArgumentParser):
    structured_errors = False
    json_error_mode = False
    operation = "cli"
    _positionals_only = False
    _defer_value_errors = False

    def _parse_optional(self, arg_string):
        return None if self._positionals_only else super()._parse_optional(arg_string)

    def _invalid_option_value(self, action: argparse.Action) -> ArgumentError:
        option = action.option_strings[0]
        details: dict[str, object] = {"option": option}
        message = f"invalid value for {option}"
        if action.choices is not None:
            details["choices"] = list(action.choices)
            message += "; choose from: " + ", ".join(str(value) for value in action.choices)
        elif action.type in {int, float}:
            message += "; an integer is required" if action.type is int else "; a number is required"
        return argument_error(message, "invalid_value", **details)

    def _get_value(self, action, arg_string):
        try:
            return super()._get_value(action, arg_string)
        except argparse.ArgumentError:
            if not self._defer_value_errors:
                raise
            self._value_errors.append(self._invalid_option_value(action))
            return arg_string

    def _check_value(self, action, value):
        if self._defer_value_errors and action.choices is not None and value not in action.choices:
            self._value_errors.append(self._invalid_option_value(action))
            return
        super()._check_value(action, value)

    def __init__(self, *args, **kwargs):
        kwargs["allow_abbrev"] = False
        super().__init__(*args, **kwargs)

    def _scan(self, source: Sequence[str]) -> tuple[list[str], list[str], list[str], bool]:
        options: list[str] = []
        words: list[str] = []
        explicit: list[str] = []
        index = 0
        while index < len(source):
            token = source[index]
            if token == "--":
                words.extend(source[index + 1 :])
                break
            if token in {"--help", "-h"}:
                return options, words, explicit, True
            name, separator, _ = token.partition("=")
            action = self._option_string_actions.get(name)
            if not token.startswith("-") or token == "-":
                words.append(token)
                index += 1
                continue
            if action is None:
                option = safe_option(token)
                raise argument_error(f"unrecognized option: {option}", "unknown_option", option=option)
            explicit.append(action.option_strings[0])
            options.append(token)
            if action.nargs != 0 and not separator:
                if index + 1 == len(source) or (
                    source[index + 1].startswith("-") and not _negative_number(source[index + 1])
                ):
                    raise argument_error(f"{name} requires a value", "missing_value", option=name)
                options[-1] = f"{token}={source[index + 1]}"
                index += 1
            index += 1
        return options, words, explicit, False

    def _context(self, source: Sequence[str]) -> None:
        """Recover output mode/command using option arity, never option values."""
        index = 0
        command_seen = False
        while index < len(source):
            token = source[index]
            if token == "--":
                break
            name, separator, _ = token.partition("=")
            action = self._option_string_actions.get(name)
            if action is not None and action.dest == "json_output" and action.nargs == 0 and not separator:
                self.json_error_mode = True
            if token.startswith("-") and action is None:
                command_seen = True
            if action is not None and action.nargs != 0 and not separator:
                next_is_option = (
                    index + 1 < len(source)
                    and source[index + 1].startswith("-")
                    and not _negative_number(source[index + 1])
                )
                if next_is_option or index + 1 == len(source):
                    command_seen = True
                index += 1 if next_is_option else 2
                continue
            if not token.startswith("-") and not command_seen:
                command_seen = True
                if token in _COMMAND_NAMES:
                    self.operation = token
                elif is_http_url(token):
                    self.operation = "download"
            index += 1

    def _help_target(self, words: Sequence[str], *, strict: bool = True) -> argparse.ArgumentParser:
        target: argparse.ArgumentParser = self
        for word in words:
            sub = next((a for a in target._actions if isinstance(a, argparse._SubParsersAction)), None)
            if sub is None or word not in sub.choices:
                if not strict and target is not self:
                    return target
                choices = sorted(sub.choices) if sub is not None else []
                raise argument_error(
                    "unknown or excess help target", "help_target", argument="command", choices=choices
                )
            target = sub.choices[word]
        return target

    def _grammar_parsers(self) -> list[_CliArgumentParser]:
        parsers = [self]
        for action in self._actions:
            if isinstance(action, argparse._SubParsersAction):
                for child in action.choices.values():
                    parsers.extend(child._grammar_parsers())
        return parsers

    def parse_args(self, args=None, namespace=None):
        source = list(sys.argv[1:] if args is None else args)
        self.json_error_mode = False
        self.operation = "cli"
        self._context(source)
        try:
            options, words, explicit, help_requested = self._scan(source)
            if words and words[0] == "help":
                target = self._help_target(words[1:])
                # Check option syntax preceding help without executing anything.
                argparse.ArgumentParser.parse_known_args(self, options, namespace)
                target.print_help()
                self.exit(0)
            if help_requested:
                argparse.ArgumentParser.parse_known_args(self, options, namespace)
                self._help_target(words, strict=False).print_help()
                self.exit(0)
            if words and is_http_url(words[0]):
                words.insert(0, "download")
            # Options are parsed once, independently of the positional hierarchy.
            grammar = self._grammar_parsers()
            for parser in grammar:
                parser._positionals_only = True
            try:
                parsed = super().parse_args(words, namespace)
            finally:
                for parser in grammar:
                    parser._positionals_only = False
            self._value_errors: list[ArgumentError] = []
            self._defer_value_errors = True
            try:
                parsed, _ = argparse.ArgumentParser.parse_known_args(self, options, parsed)
            finally:
                self._defer_value_errors = False
            parsed._value_errors = tuple(self._value_errors)
            parsed._explicit_options = frozenset(explicit)
            parsed._option_order = tuple(explicit)
            empty_values = {token.partition("=")[0]: token.partition("=")[2] == "" for token in options if "=" in token}
            parsed._empty_options = frozenset(name for name, empty in empty_values.items() if empty)
            parsed.config_explicit = "--config" in explicit
            parsed.command_args = _compatibility_command_args(parsed)
            if not self.structured_errors:
                from .validation import _check_conflicts

                _check_conflicts(parsed, {option.removeprefix("--") for option in explicit}, syntax_only=True)
                if self._value_errors:
                    raise self._value_errors[0]
            return parsed
        except ArgumentError as exc:
            if self.structured_errors:
                raise
            self.exit(2, f"error [argument_error]: {exc}\n")

    def error(self, message: str) -> NoReturn:
        # argparse embeds raw invalid values in its messages. Retain grammar
        # labels and registry choices, never those values.
        if "invalid choice:" in message and not message.startswith("argument --"):
            choices = re.findall(r"'([A-Za-z][A-Za-z0-9-]*)'", message.split("choose from", 1)[-1])
            raise argument_error(
                "unknown command or subcommand; choose from: " + ", ".join(choices),
                "unknown_command",
                argument="command",
                choices=choices,
            )
        if message.startswith("the following arguments are required:"):
            argument = message.split(":", 1)[1].strip()
            raise argument_error(f"required argument missing: {argument}", "missing_argument", argument=argument)
        option = next((name for name in self._option_string_actions if message.startswith(f"argument {name}:")), None)
        if option is not None:
            option_choices = self._option_string_actions[option].choices
            details: dict[str, object] = {"option": option}
            if option_choices is not None:
                details["choices"] = list(option_choices)
            raise argument_error(f"invalid value for {option}", "invalid_value", **details)
        raise argument_error("unexpected positional argument", "unexpected_argument", argument="positional")


def _compatibility_command_args(args: argparse.Namespace) -> list[str]:
    command = getattr(args, "command", None)
    action = getattr(args, "action", None)
    if command == "config":
        result = [action] if action else []
        if action == "profile":
            result.extend([args.profile_action, args.profile_name])
        elif action == "init" and args.destination is not None:
            result.append(args.destination)
        return result
    if command == "verify":
        return [args.verify_resource, args.target]
    if command == "plugin":
        return [action] + ([args.target] if getattr(args, "target", None) is not None else [])
    if command == "state":
        result = ["workflow"]
        workflow_action = getattr(args, "workflow_action", None)
        if workflow_action is not None:
            result.append(workflow_action)
            if getattr(args, "target", None) is not None:
                result.append(args.target)
        return result
    return []


def build_parser() -> argparse.ArgumentParser:
    parser = _CliArgumentParser(description="local plugin API v3 image downloader")
    parser.set_defaults(config_explicit=False, command=None)
    for option in OPTIONS:
        option.add_to(parser)
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")
    for name in ("download", "workflow", "inspect", "doctor", "cookie", "config", "plugin", "state", "verify"):
        command = commands.add_parser(name, help=f"{name} operations")
        command.set_defaults(command_handler=name)
        # Help lists canonical options from the single root registry. Parsing
        # strips them before entering this hierarchy, so there are no competing
        # per-level defaults or mutually-exclusive groups.
        command.epilog = "Options may appear before, within, or after the command. See --help at the root."
        if name in {"download", "workflow", "inspect"}:
            command.add_argument("url")
        elif name == "verify":
            command.description = "Check recorded errors and file presence; does not validate image contents."
            actions = command.add_subparsers(dest="verify_resource", required=True)
            actions.add_parser("logs", help="Check all recorded errors, not artifact integrity").add_argument("target")
            actions.add_parser("workflow", help="Check selected URLs and file presence using RUN_ID").add_argument(
                "target"
            )
        elif name == "cookie":
            actions = command.add_subparsers(dest="cookie_action", required=True, metavar=None)
            for action in ("export", "import", "browser-import"):
                actions.add_parser(action).add_argument("cookie_value")
        elif name == "config":
            actions = command.add_subparsers(dest="action", required=True, metavar=None)
            actions.add_parser("path")
            actions.add_parser("explain")
            actions.add_parser("init").add_argument("destination", nargs="?")
            profiles = actions.add_parser("profile").add_subparsers(dest="profile_action", required=True, metavar=None)
            profiles.add_parser("init").add_argument("profile_name")
        elif name == "plugin":
            actions = command.add_subparsers(dest="action", required=True, metavar=None)
            actions.add_parser("list")
            for action in ("install", "trust", "revoke", "uninstall"):
                actions.add_parser(action).add_argument("target")
        elif name == "state":
            groups = command.add_subparsers(dest="state_group", required=True, metavar=None)
            actions = groups.add_parser("workflow").add_subparsers(dest="workflow_action", metavar=None)
            for action in ("list", "show", "run", "history", "prune"):
                operation = actions.add_parser(action)
                if action in {"show", "run"}:
                    operation.add_argument("target")
                elif action == "history":
                    operation.add_argument("target", nargs="?")
    return parser
