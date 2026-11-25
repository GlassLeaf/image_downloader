"""Build scoped help from the existing option registry and real command tree."""

from __future__ import annotations

import argparse
import re
from collections.abc import Sequence
from pathlib import Path

from .._cli_help import (
    CommandHelp,
    HelpEntry,
    HelpSection,
    argument_sections,
    children,
    leaf_paths,
    option_details,
    render_help,
    subcommand_usage,
)
from .help_catalog import COMMANDS, PROGRAM, ROOT_GROUPS
from .option_help import GROUPS
from .options import CONFLICT_GROUPS, LEGACY_COOKIE_OPTIONS, OptionDefinition


def _scope(path: str) -> str:
    return "cookie" if path.startswith("cookie ") else path


def visible_options(path: str, definitions: Sequence[OptionDefinition]) -> tuple[OptionDefinition, ...]:
    """Include conditional options with notes; omit options forbidden by the selected action/mode."""
    scope = "download" if path == "download --list-updated-urls" else _scope(path)
    excluded = set(LEGACY_COOKIE_OPTIONS)
    if path.startswith("cookie ") and path != "cookie browser-import":
        excluded.add("chrome-user-data-dir")
    if path == "download --inspect-only":
        excluded.add("list-updated-urls")
    elif path == "download --list-updated-urls":
        excluded.update(("inspect-only", "force-image-format"))
    return tuple(o for o in definitions if scope in o.commands and o.name not in excluded)


def _description(option: OptionDefinition, path: str) -> str:
    description = option.help or "Additional option for this operation."
    if option.name == "plugin" and path.startswith("state"):
        description = "Filter saved registrations by plugin ID."
        if path in {"state", "state workflow", "state workflow targets", "state workflow set", "state workflow reset"}:
            description += " With a live query, select the update-check plugin."
    elif option.name == "output-dir" and path == "verify workflow":
        description = "Inspect recorded output files under this absolute directory. Required; does not change files."
    elif option.name == "dry-run" and path == "workflow":
        description = (
            "Preview the first update-check selection without saving state or downloading images. "
            "Authentication and network requests may still occur."
        )
    elif option.name == "dry-run" and path.startswith("state"):
        description = "Show planned history pruning or status changes without prompting or saving them."
    elif option.name == "plugin-root" and path.split(" ", 1)[0] in {"cookie", "credential", "vault"}:
        description = (
            "Override plugins.root during configuration resolution; credential and cookie storage "
            "still uses the selected data root."
        )
    elif option.name == "json" and path in {"inspect", "download --inspect-only"}:
        description = "Write compact inspection JSON instead of formatted inspection JSON."
    elif option.name == "config" and path == "config profile init":
        description = (
            "Select the absolute main configuration path; create it if absent before creating profile settings."
        )
    elif path == "config init" and option.name in {"data-root", "plugin-root"}:
        description = f"Write this absolute {option.name} directory into the new configuration."
    if path in {"state workflow targets", "state workflow set", "state workflow reset"} and option.name in {
        "plugin-root",
        "plugin-verification-override",
        "download-scope",
    }:
        description += " Requires --query-targets-from-workflow-url."
    return description


def _label(option: OptionDefinition) -> str:
    if option.flag:
        return "--" + option.name
    value = option.metavar or ("PATH" if option.converter is Path else option.name.replace("-", "_").upper())
    return f"--{option.name} {value}"


def _noop(option: OptionDefinition, path: str) -> bool:
    base = "download" if path.startswith("download --") else _scope(path)
    return path in option.no_effect or base in option.no_effect


def _entry(option: OptionDefinition, path: str, *, inactive: bool = False) -> HelpEntry:
    if inactive:
        description = {
            "yes": "Accepted for compatibility; this operation does not request confirmation.",
            "plugin-verification-override": "Accepted, but this operation does not perform plugin verification.",
        }.get(option.name, "Accepted, but this mode does not allocate or save image output.")
    else:
        description = _description(option, path)
    omitted = option.omitted
    if option.name == "plugin" and path.startswith("state"):
        omitted = "all saved plugin IDs"
        if path in {"state workflow targets", "state workflow set", "state workflow reset"}:
            omitted += "; automatic selection for a live query"
    elif option.name == "json" and path in {"inspect", "download --inspect-only"}:
        omitted = "formatted inspection JSON"
    return HelpEntry(
        _label(option),
        option_details(
            description,
            choices=option.choices,
            repeat=option.repeat,
            default=option.default if not inactive else None,
            omitted=omitted if not inactive else "",
        ),
    )


def _option_sections(path: str, options: Sequence[OptionDefinition]) -> list[HelpSection]:
    groups: dict[str, list[HelpEntry]] = {}
    inactive = []
    for option in options:
        if _noop(option, path):
            inactive.append(_entry(option, path, inactive=True))
        else:
            groups.setdefault(option.group, []).append(_entry(option, path))
    order = (*GROUPS, *(name for name in groups if name not in GROUPS))
    sections = [HelpSection(name, tuple(groups.get(name, ()))) for name in order]
    sections.append(HelpSection("Help", (HelpEntry("-h, --help", "Show this help and exit."),)))
    sections.append(HelpSection("Accepted without effect", tuple(inactive)))
    return sections


def _requirements(path: str, options: Sequence[OptionDefinition], registered: set[str]) -> list[str]:
    names = {o.name for o in options}
    notes = []
    for conflict in CONFLICT_GROUPS:
        present = ["--" + name for name in conflict if name in names]
        if len(present) > 1:
            notes.append("Cannot combine " + " and ".join(present) + ".")
    if {"fallback-generic", "plugin", "force-plugin"} <= names:
        notes.append("Explicit --fallback-generic enabled/disabled cannot be combined with --plugin or --force-plugin.")
    if path in {"state workflow targets", "state workflow set", "state workflow reset"}:
        notes.append(
            "Choose at most one of TARGET_URL, --url-regex, or --all. "
            "--filter-workflow-url filters saved registrations; the query option performs a live update check."
        )
        if path != "state workflow targets":
            notes.append(
                "A TARGET_URL, --url-regex, or --all selector is required. "
                "Changes require per-URL confirmation; --yes is not accepted. --dry-run skips prompts."
            )
        if path == "state workflow set":
            notes.append("--status is required.")
    if path.startswith("download --"):
        other = "list-updated-urls" if path.endswith("inspect-only") else "inspect-only"
        if other in registered:
            notes.append(f"This mode cannot be combined with --{other}.")
    return notes


def _group_options(
    parser: argparse.ArgumentParser, path: tuple[str, ...], definitions: Sequence[OptionDefinition]
) -> tuple[list[HelpSection], list[HelpEntry]]:
    leaves = leaf_paths(parser, path)
    available = {leaf: {o.name for o in visible_options(leaf, definitions)} for leaf in leaves}
    shared, differing = [], []
    for option in definitions:
        accepted = [leaf for leaf in leaves if option.name in available[leaf]]
        if not accepted:
            continue
        inactive = [leaf for leaf in accepted if _noop(option, leaf)]
        if len(accepted) == len(leaves) and (not inactive or len(inactive) == len(leaves)):
            shared.append(option)
        else:
            detail = _description(option, " ".join(path)) + " Accepted by: " + ", ".join(accepted) + "."
            if inactive:
                detail += " Without effect in: " + ", ".join(inactive) + "."
            differing.append(
                HelpEntry(
                    _label(option),
                    option_details(
                        detail,
                        choices=option.choices,
                        repeat=option.repeat,
                        default=option.default,
                        omitted=option.omitted,
                    ),
                )
            )
    # A common ignored option must be classified using a real leaf scope.
    return _option_sections(leaves[0], shared), differing


def _legacy(definitions: Sequence[OptionDefinition]) -> HelpSection:
    forms = []
    for option in definitions:
        if option.name in LEGACY_COOKIE_OPTIONS:
            forms.append(f"{PROGRAM} {_label(option)}  ({option.help})")
    return HelpSection(
        "Legacy invocation forms",
        (
            HelpEntry(
                "Standalone cookie operations",
                "Choose exactly one legacy operation without a command. "
                "These options cannot be combined with cookie subcommands or other commands.",
            ),
        ),
        tuple(forms),
    )


def _examples(spec: CommandHelp, definitions: Sequence[OptionDefinition]) -> HelpSection:
    registered = {"--" + option.name for option in definitions}
    examples = tuple(
        example for example in spec.examples if set(re.findall(r"(?<!\S)--[a-z][a-z-]*", example)) <= registered
    )
    return HelpSection("Examples", paragraphs=examples)


def format_help(
    parser: argparse.ArgumentParser,
    path: tuple[str, ...],
    definitions: Sequence[OptionDefinition],
    *,
    prog: str,
    modes: frozenset[str] = frozenset(),
) -> str:
    command = " ".join(path)
    selected = modes & {"inspect-only", "list-updated-urls"} if command == "download" else frozenset()
    if len(selected) == 1:
        command += " --" + next(iter(selected))
    spec = COMMANDS.get(command, CommandHelp("Additional CLI operation.", ()))
    if not path:
        sections = [
            HelpSection(
                title, tuple(HelpEntry(name, COMMANDS[name].summary) for name in names if name in children(parser))
            )
            for title, names in ROOT_GROUPS
        ]
        sections.extend(
            (
                HelpSection(
                    "Help",
                    (HelpEntry("-h, --help", "Show help and exit."),),
                    (
                        "Use help COMMAND [SUBCOMMAND ...] or COMMAND --help for options, conditions, and examples. "
                        "Options are specific to each operation; their position does not make them global.",
                        "A bare HTTP(S) URL is a compatibility form of download URL. See help cookie for legacy forms.",
                    ),
                ),
                _examples(spec, definitions),
            )
        )
        return render_help(f"{prog} COMMAND [arguments] [options]", spec.summary, sections)
    arguments, argument_section = argument_sections(parser, spec)
    sections = [argument_section]
    branch = children(parser)
    if branch:
        sections.append(
            HelpSection(
                "Subcommands",
                tuple(HelpEntry(name, COMMANDS.get(" ".join((*path, name)), spec).summary) for name in branch),
            )
        )
        sections.append(
            HelpSection(
                "Shared options",
                paragraphs=(
                    "The grouped options below are accepted by every subcommand. Differences are listed under "
                    "Options by subcommand; see each operation's help for conditional requirements.",
                ),
            )
        )
        common, different = _group_options(parser, path, definitions)
        sections.extend(s for s in common if s.title != "Accepted without effect")
        sections.append(HelpSection("Options by subcommand", tuple(different)))
        requirements = list(spec.notes)
        inactive = [s for s in common if s.title == "Accepted without effect"]
    else:
        options = visible_options(command, definitions)
        option_sections = _option_sections(command, options)
        # Constraints precede the separate compatibility section.
        sections.extend(s for s in option_sections if s.title != "Accepted without effect")
        requirements = list(spec.notes) + _requirements(command, options, {o.name for o in definitions})
        inactive = [s for s in option_sections if s.title == "Accepted without effect"]
    if len(selected) > 1:
        requirements.append("--inspect-only and --list-updated-urls cannot be combined; showing normal download help.")
    sections.append(HelpSection("Requirements and conflicts", paragraphs=tuple(requirements)))
    sections.extend(inactive)
    if path == ("cookie",):
        sections.append(_legacy(definitions))
    sections.append(_examples(spec, definitions))
    if path == ("download",):
        sections.append(
            HelpSection(
                "Other modes",
                paragraphs=tuple(
                    f"{PROGRAM} help download --{mode}"
                    for mode in ("inspect-only", "list-updated-urls")
                    if any(o.name == mode for o in definitions)
                ),
            )
        )
    sections.append(
        HelpSection("Related commands", paragraphs=tuple(f"{PROGRAM} help {name}" for name in spec.related))
    )
    tail = f" {subcommand_usage(parser)} [arguments]" if branch else (" " + " ".join(arguments) if arguments else "")
    mode = command.removeprefix("download") if command.startswith("download --") else ""
    return render_help(f"{prog} {' '.join(path)}{tail}{mode} [options]", spec.summary, sections)
