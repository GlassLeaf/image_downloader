"""Plain-text help building blocks, independent of configuration and execution."""

from __future__ import annotations

import argparse
import shutil
import textwrap
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class CommandHelp:
    summary: str
    examples: tuple[str, ...]
    arguments: tuple[tuple[str, str], ...] = ()
    notes: tuple[str, ...] = ()
    related: tuple[str, ...] = ()


@dataclass(frozen=True)
class HelpEntry:
    label: str
    description: str


@dataclass(frozen=True)
class HelpSection:
    title: str
    entries: tuple[HelpEntry, ...] = ()
    paragraphs: tuple[str, ...] = ()


def children(parser: argparse.ArgumentParser) -> Mapping[str, argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    return {}


def walk_parsers(
    parser: argparse.ArgumentParser, path: tuple[str, ...] = ()
) -> Iterator[tuple[tuple[str, ...], argparse.ArgumentParser]]:
    yield path, parser
    for name, child in children(parser).items():
        yield from walk_parsers(child, (*path, name))


def leaf_paths(parser: argparse.ArgumentParser, path: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(" ".join(words) for words, child in walk_parsers(parser, path) if not children(child))


def subcommand_usage(parser: argparse.ArgumentParser) -> str:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return "SUBCOMMAND" if action.required else "[SUBCOMMAND]"
    return ""


def argument_sections(parser: argparse.ArgumentParser, spec: CommandHelp) -> tuple[list[str], HelpSection]:
    actions = [a for a in parser._actions if not a.option_strings and not isinstance(a, argparse._SubParsersAction)]
    entries = []
    usage = []
    for index, action in enumerate(actions):
        label, description = (
            spec.arguments[index]
            if index < len(spec.arguments)
            else (str(action.metavar or action.dest.upper()), action.help or "Value for this operation.")
        )
        usage.append(f"[{label}]" if action.nargs in {"?", "*"} else label)
        entries.append(HelpEntry(label, description))
    return usage, HelpSection("Arguments", tuple(entries))


def option_label(action: argparse.Action) -> str:
    label = ", ".join(action.option_strings)
    if action.nargs != 0:
        label += " " + str(action.metavar or action.dest.upper())
    return label


def option_details(
    description: str,
    *,
    choices: Sequence[object] | None = None,
    repeat: bool = False,
    default: object = None,
    omitted: str = "",
) -> str:
    details = [description]
    if choices is not None:
        details.append("Choices: " + ", ".join(str(value) for value in choices) + ".")
    if repeat:
        details.append("May be repeated.")
    if omitted:
        details.append("Omitted: " + omitted.rstrip(".") + ".")
    elif repeat:
        details.append("Omitted: no additional values.")
    elif default is not None and default != argparse.SUPPRESS:
        details.append(
            f"Default: {default}."
            if not isinstance(default, bool)
            else "Omitted: enabled."
            if default
            else "Omitted: disabled."
        )
    return " ".join(details)


def render_help(usage: str, summary: str, sections: Sequence[HelpSection]) -> str:
    """Keep labels and examples intact; wrap prose without clipping long names."""
    width = max(40, shutil.get_terminal_size((80, 24)).columns - 2)

    def prose(value: str, indent: int = 0) -> list[str]:
        return textwrap.wrap(
            value,
            width=width,
            initial_indent=" " * indent,
            subsequent_indent=" " * indent,
            break_long_words=False,
            break_on_hyphens=False,
        )

    lines = ["usage: " + usage, "", *prose(summary)]
    for section in sections:
        if not section.entries and not section.paragraphs:
            continue
        lines.extend(("", section.title + ":"))
        for entry in section.entries:
            lines.append("  " + entry.label)
            lines.extend(prose(entry.description, 4))
        for paragraph in section.paragraphs:
            if section.title in {"Examples", "Other modes", "Related commands", "Legacy invocation forms"}:
                lines.append("  " + paragraph)
            else:
                lines.extend(prose(paragraph, 2))
    return "\n".join(lines) + "\n"
