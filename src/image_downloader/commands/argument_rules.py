"""Shared grammar predicates and conflict rules; independent of parser execution."""

from __future__ import annotations

import argparse
from urllib.parse import urlsplit

from ..diagnostics import argument_error
from .options import CONFLICT_GROUPS, LEGACY_COOKIE_OPTIONS, OPTIONS

_OPTION_NAMES = tuple(option.name for option in OPTIONS)
_LEGACY_COOKIE = LEGACY_COOKIE_OPTIONS


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
