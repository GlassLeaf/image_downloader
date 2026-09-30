"""Shared syntax for output-format plugin tokens."""

from __future__ import annotations

import re

# Plugin IDs deliberately use the same reverse-DNS spelling as the plugin
# catalog.  Plugin-provided keys use output-token-style upper snake case.
PLUGIN_ID_PATTERN = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+"
PLUGIN_KEY_PATTERN = r"[A-Z][A-Z0-9_]{0,63}"
PLUGIN_TOKEN_PATTERN = re.compile(rf"%PLUGIN\[(?P<plugin_id>{PLUGIN_ID_PATTERN}):(?P<key>{PLUGIN_KEY_PATTERN})\]%")
PLUGIN_TOKEN_PREFIX = "%PLUGIN["


def has_invalid_plugin_token(value: str) -> bool:
    """Return whether a ``%PLUGIN[...]%`` sequence has invalid syntax."""
    position = 0
    while True:
        start = value.find(PLUGIN_TOKEN_PREFIX, position)
        if start == -1:
            return False
        match = PLUGIN_TOKEN_PATTERN.match(value, start)
        if match is None:
            return True
        position = match.end()
