"""Development CLI help, built from the actual argparse actions."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import cast

from .._cli_help import (
    CommandHelp,
    HelpEntry,
    HelpSection,
    argument_sections,
    children,
    option_details,
    option_label,
    render_help,
    walk_parsers,
)
from .catalog import FEATURES

PROGRAM = "image-downloader-dev"
TARGET = '"C:\\Plugin Projects\\gallery"'
URL = "https://example.test/gallery"
GROUPS = (
    "Generation",
    "Plugin loading",
    "Configuration and profile",
    "Fixtures and simulation",
    "Call arguments",
    "Signing",
    "Output location and data",
    "Execution limits",
    "Display and logging",
)
ROOT_GROUPS = (
    ("Generate and sign", ("init", "extend", "sign")),
    ("Inspect and check", ("load", "methods", "check")),
    ("Run and debug", ("call", "simulate", "session")),
)


def _command(
    summary: str, name: str, suffix: str = "", variant: str = "--json", notes: tuple[str, ...] = ()
) -> CommandHelp:
    base = f"{PROGRAM} {name} {TARGET}" + (" " + suffix if suffix else "")
    arguments: tuple[tuple[str, str], ...] = (
        (
            "TARGET",
            {
                "init": "Destination directory for the new plugin project.",
                "extend": "Existing generated plugin project directory.",
                "sign": "Plugin project or plugin source directory to sign.",
            }.get(name, "Plugin project, plugin source directory, or Python source file."),
        ),
    )
    if name == "call":
        arguments += (("METHOD", "Method name, or reference.method within a persistent session."),)
    elif name == "simulate":
        arguments += (("URL", "Absolute HTTP(S) page URL to inspect or download."),)
    return CommandHelp(summary, (base, f"{base} {variant}"), arguments, notes)


COMMANDS = {
    "": CommandHelp(
        "Generate, inspect, run, and debug local API v3 plugins in isolated development workers.",
        (f"{PROGRAM} init {TARGET}", f"{PROGRAM} help simulate", f"{PROGRAM} load {TARGET} --json"),
    ),
    "init": _command(
        "Generate a plugin project, configuration, fixtures, and tests.",
        "init",
        variant="--features host,html --non-interactive",
    ),
    "extend": _command(
        "Preview feature additions to an existing generated project; use --apply to write changes.",
        "extend",
        "--features updates --non-interactive",
        "--apply",
    ),
    "load": _command("Load and validate a plugin, then show its identity, class, and methods.", "load"),
    "methods": _command("Load a plugin and list callable method signatures.", "methods"),
    "call": _command(
        "Call one plugin method and display its result.",
        "call",
        "matches",
        "--url https://example.test/gallery",
        (
            "--input replaces --args/--kwargs. Named object references exist only in the worker process; "
            "they do not persist between separate CLI invocations.",
        ),
    ),
    "simulate": _command(
        "Inspect or download a page using the core runtime and local plugin sources.",
        "simulate",
        URL,
        "--mode download --record",
        (
            "preview resolves the manifest and requests; download acquires files. "
            "replay uses the download path and requires --fixture. Real network access is possible "
            "without a fixture.",
        ),
    ),
    "session": _command(
        "Open a persistent development session with reusable objects and live source reload.",
        "session",
        notes=(
            "Session commands: load, methods, call METHOD {args,kwargs,save_as}, "
            "simulate URL [preview|download|replay], check, refs, reload, quit (or exit). "
            "For example, enter methods, then simulate https://example.test/gallery preview. "
            "reload cleans up and discards named references before loading the source again.",
        ),
    ),
    "check": _command(
        "Check for unimplemented hooks and execute configured validation scenarios.",
        "check",
        variant='--scenario "C:\\Plugin Projects\\gallery\\scenarios\\basic.yaml"',
    ),
    "sign": _command(
        "Sign a plugin source tree with a publisher key stored outside that source tree.",
        "sign",
        '--key "C:\\Publisher Keys\\publisher.pem"',
        "--create-key",
        ("--key is required and must point outside the plugin source tree.",),
    ),
}

OPTION_HELP = {
    "id": ("Generation", "Set the generated plugin identifier.", "uses answers or the generation default"),
    "kind": ("Plugin loading", "Select the plugin kind.", "infer from the target or generation answers"),
    "class": ("Plugin loading", "Select the plugin class by name.", "infer from the target or generation answers"),
    "features": (
        "Generation",
        "Generate the comma-separated feature IDs listed below.",
        "uses answers or the interactive wizard",
    ),
    "answers": (
        "Generation",
        "Read generation answers from a YAML or JSON file; explicit flags take priority.",
        "no answers file",
    ),
    "non-interactive": ("Generation", "Disable the generation wizard and use supplied answers and defaults.", ""),
    "apply": (
        "Generation",
        "Apply the proposed extension, preserving edited files and reporting conflicts.",
        "show the proposed diff without applying it",
    ),
    "config": (
        "Configuration and profile",
        "Read this plugin's settings from a YAML or JSON file, not the application's main configuration.",
        "uses project settings",
    ),
    "app-config": (
        "Configuration and profile",
        "Merge application settings from a YAML or JSON file. "
        "Development output roots and disabled notifications remain enforced.",
        "uses project settings",
    ),
    "profile": (
        "Configuration and profile",
        "Read existing credentials on demand and copy cookies from this profile. Changes stay in the development run.",
        "use development credentials and cookies",
    ),
    "profile-data-root": (
        "Configuration and profile",
        "Locate the source vault under this directory. Only used when --profile is supplied.",
        "the standard data root",
    ),
    "fixture": (
        "Fixtures and simulation",
        "Use recorded HTTP fixtures instead of live HTTP transport.",
        "live HTTP transport",
    ),
    "processor": ("Plugin loading", "Load an additional image processor from this source path.", ""),
    "record": ("Fixtures and simulation", "Record HTTP exchanges to recording.yaml in the run directory.", ""),
    "force-plugin": ("Fixtures and simulation", "Bypass the selected site plugin's URL matcher during simulation.", ""),
    "mode": (
        "Fixtures and simulation",
        "Choose inspection, download, or fixture-backed download. replay requires --fixture.",
        "",
    ),
    "scenario": (
        "Fixtures and simulation",
        "Run a scenario YAML or JSON file containing method calls and expectations.",
        "use configured project scenarios",
    ),
    "input": (
        "Call arguments",
        "Read call inputs from YAML or JSON; replaces --args and --kwargs. "
        "Relative resources are resolved against this file.",
        "use --args and --kwargs",
    ),
    "args": ("Call arguments", "Supply positional method arguments as a JSON array.", ""),
    "kwargs": ("Call arguments", "Supply keyword method arguments as a JSON object.", ""),
    "save-as": (
        "Call arguments",
        "Name the result in the worker's object references; names are not persisted between separate CLI invocations.",
        "",
    ),
    "url": (
        "Call arguments",
        "Set the default URL for the execution context and official method arguments.",
        "project sample URL, or https://example.test/",
    ),
    "key": (
        "Signing",
        "Read the publisher signing key from this path. Required and outside the plugin source tree.",
        "required",
    ),
    "create-key": ("Signing", "Create the signing key if it does not already exist.", ""),
    "run-root": (
        "Output location and data",
        "Create the isolated run directory under this parent directory.",
        "a new directory under the project's .plugin-dev/runs",
    ),
    "binary": (
        "Output location and data",
        "Always save binary bodies to files; optionally include their hex or Base64 representation in displayed data.",
        "",
    ),
    "timeout": ("Execution limits", "Maximum wait per worker reply, in finite positive seconds.", ""),
    "json": (
        "Display and logging",
        "Write compact JSON results to stdout; diagnostics use stderr. Help remains plain text.",
        "formatted result output",
    ),
    "log-view": (
        "Display and logging",
        "Choose the console preset: summary shows INFO+ events and Python logs; "
        "all shows DEBUG+ and all output sources. Stored diagnostics are unchanged.",
        "",
    ),
    "log-level": (
        "Display and logging",
        "Set the minimum console log level, overriding the preset; case-insensitive. Stored diagnostics are unchanged.",
        "INFO, or DEBUG with --log-view all",
    ),
    "log-source": (
        "Display and logging",
        "Select console sources, replacing the preset's sources. Stored diagnostics are unchanged.",
        "sources from --log-view",
    ),
    "event": ("Display and logging", "Include event names matching this case-sensitive glob.", ""),
    "exclude-event": (
        "Display and logging",
        "Exclude event names matching this case-sensitive glob; exclusions win.",
        "",
    ),
    "logger": ("Display and logging", "Include only Python loggers matching this case-sensitive glob.", ""),
    "exclude-logger": ("Display and logging", "Exclude Python loggers matching this case-sensitive glob.", ""),
}


def _options(parser: argparse.ArgumentParser, command: str) -> list[HelpSection]:
    groups: dict[str, list[HelpEntry]] = {}
    for action in parser._actions:
        if not action.option_strings or action.dest == "help":
            continue
        name = action.option_strings[0].removeprefix("--")
        group, description, omitted = OPTION_HELP.get(name, ("Other options", action.help or "Additional option.", ""))
        if command in {"init", "extend"} and name in {"id", "kind", "class"}:
            group = "Generation"
        if name == "features":
            description += " Features: " + ", ".join(feature.id for feature in FEATURES) + "."
        if command == "call" and name == "save-as":
            group = "Accepted without effect"
            description = (
                "Accepted, but a one-shot call closes the worker after returning its result. "
                "Use session to reuse named object references."
            )
        groups.setdefault(group, []).append(
            HelpEntry(
                option_label(action),
                option_details(
                    description,
                    choices=tuple(action.choices) if action.choices is not None else None,
                    repeat=isinstance(action, argparse._AppendAction),
                    default=action.default,
                    omitted=omitted,
                ),
            )
        )
    order = (*GROUPS, *(name for name in groups if name not in GROUPS))
    logging_notes = (
        (
            "Console filters do not change stored diagnostics. Repeated patterns within one filter use OR; "
            "different filters use AND. Exclusion patterns take priority.",
        )
        if any(a.dest == "log_view" for a in parser._actions)
        else ()
    )
    return [
        HelpSection(name, tuple(groups.get(name, ())), logging_notes if name == "Display and logging" else ())
        for name in order
    ]


def format_help(parser: argparse.ArgumentParser, path: tuple[str, ...]) -> str:
    name = " ".join(path)
    spec = COMMANDS[name]
    if not path:
        sections = [
            HelpSection(
                title,
                tuple(
                    HelpEntry(command, COMMANDS[command].summary) for command in names if command in children(parser)
                ),
            )
            for title, names in ROOT_GROUPS
        ]
        sections.extend(
            (
                HelpSection(
                    "Help",
                    (HelpEntry("-h, --help", "Show help and exit."),),
                    ("Use help COMMAND or COMMAND --help for grouped options and examples.",),
                ),
                HelpSection("Examples", paragraphs=spec.examples),
            )
        )
        return render_help(f"{PROGRAM} COMMAND [arguments] [options]", spec.summary, sections)
    usage, arguments = argument_sections(parser, spec)
    required = [option_label(a) for a in parser._actions if a.option_strings and a.required]
    options = _options(parser, name)
    sections = [
        arguments,
        *(section for section in options if section.title != "Accepted without effect"),
        HelpSection("Help", (HelpEntry("-h, --help", "Show this help and exit."),)),
        HelpSection("Requirements and conflicts", paragraphs=spec.notes),
        *(section for section in options if section.title == "Accepted without effect"),
        HelpSection("Examples", paragraphs=spec.examples),
    ]
    return render_help(f"{PROGRAM} {name} {' '.join((*usage, *required))} [options]", spec.summary, sections)


class DevelopmentArgumentParser(argparse.ArgumentParser):
    """Keep normal argparse semantics; the help alias exits before any operation."""

    help_path: tuple[str, ...] | None = None

    def format_help(self) -> str:
        return super().format_help() if self.help_path is None else format_help(self, self.help_path)

    def parse_args(self, args=None, namespace=None):
        source = list(sys.argv[1:] if args is None else args)
        if self.help_path == ():
            source = self._help_alias(source)
        return super().parse_args(source, namespace)

    def _help_alias(self, source: list[str]) -> list[str]:
        index = 0
        while index < len(source) and source[index] == "--json":
            index += 1
        if index == len(source) or source[index] != "help":
            return source
        prefix = source[:index]
        tail = source[index + 1 :]
        while tail and tail[0] == "--json":
            prefix.append(tail.pop(0))
        if not tail or tail[0] in {"-h", "--help"}:
            return [*prefix, "--help"]
        target = children(self).get(tail[0])
        if target is None:
            self.error("unknown help target; choose from: " + ", ".join(children(self)))
        self._check_help_tail(target, tail[1:])
        return [*prefix, *tail, "--help"]

    def _check_help_tail(self, target: argparse.ArgumentParser, tail: Sequence[str]) -> None:
        index = 0
        while index < len(tail):
            name, equals, _ = tail[index].partition("=")
            action = target._option_string_actions.get(name)
            if action is None:
                self.error("unknown or excess help argument; use help COMMAND [options]")
            index += 2 if action.nargs != 0 and not equals else 1


def bind_help(parser: argparse.ArgumentParser) -> None:
    for path, child in walk_parsers(parser):
        cast(DevelopmentArgumentParser, child).help_path = path
