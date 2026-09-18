"""Public CLI entry points and compatibility imports."""

from .commands.config import config_command
from .commands.constants import (
    EXIT_AUTHENTICATION,
    EXIT_CONFIGURATION,
    EXIT_FAILURE,
    EXIT_PARTIAL,
    EXIT_PLUGIN,
    EXIT_SUCCESS,
)
from .commands.dispatch import main, run
from .commands.doctor import doctor
from .commands.parser import build_parser
from .commands.plugin import plugin_command

__all__ = [
    "EXIT_SUCCESS",
    "EXIT_FAILURE",
    "EXIT_CONFIGURATION",
    "EXIT_AUTHENTICATION",
    "EXIT_PLUGIN",
    "EXIT_PARTIAL",
    "build_parser",
    "run",
    "doctor",
    "config_command",
    "plugin_command",
    "main",
]
