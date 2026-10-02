"""One ordered registry for CLI types, defaults, repetition, scope, and effects."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OptionDefinition:
    name: str
    commands: frozenset[str]
    converter: Callable[[str], object] | None = None
    default: object = None
    dest: str | None = None
    repeat: bool = False
    flag: bool = False
    choices: tuple[str, ...] | None = None
    metavar: str | None = None
    help: str | None = None
    no_effect: frozenset[str] = frozenset()

    @property
    def attribute(self) -> str:
        return self.dest or self.name.replace("-", "_")

    def add_to(self, parser: argparse.ArgumentParser) -> None:
        if self.flag:
            parser.add_argument(
                "--" + self.name, dest=self.attribute, action="store_true", default=self.default, help=self.help
            )
            return
        parser.add_argument(
            "--" + self.name,
            dest=self.attribute,
            action="append" if self.repeat else "store",
            type=self.converter or str,
            choices=self.choices,
            metavar=self.metavar,
            default=[] if self.repeat else self.default,
            help=self.help,
        )


_DOWNLOAD = frozenset({"download", "workflow"})
_INSPECT = frozenset({"inspect", "download --inspect-only"})
_PLUGIN_MUTATIONS = frozenset({"plugin install", "plugin trust", "plugin revoke", "plugin uninstall"})
_PLUGIN = _PLUGIN_MUTATIONS | {"plugin list"}
_STATE = frozenset("state workflow " + action for action in ("list", "show", "run", "history", "prune"))
_CONFIG = frozenset({"config path", "config explain", "config init", "config profile init"})
_RUNTIME = _DOWNLOAD | _INSPECT | _PLUGIN | {"doctor", "cookie"}
_ALL = _RUNTIME | _STATE | _CONFIG
_OVERRIDES = _DOWNLOAD | _INSPECT | {"doctor"}
_FORMAT = _DOWNLOAD | {"doctor", "config explain"}
_OUTPUT_NOOP = frozenset({"download --inspect-only", "download --list-updated-urls"})
_IMAGE_FORMATS = ("ORIGINAL", "JPEG", "PNG", "WEBP")

OPTIONS = (
    OptionDefinition("config", _RUNTIME | _STATE | {"config explain", "config profile init"}, Path),
    OptionDefinition("profile", _RUNTIME | _STATE | {"config explain"}),
    OptionDefinition("plugin-root", _RUNTIME | {"config explain", "config init"}, Path),
    OptionDefinition("data-root", _RUNTIME | _STATE | {"config explain", "config init"}, Path),
    OptionDefinition(
        "yes",
        _RUNTIME | {"config init", "config profile init"},
        default=False,
        flag=True,
        no_effect=(_RUNTIME | {"config init", "config profile init"}) - _PLUGIN_MUTATIONS,
    ),
    OptionDefinition(
        "plugin-verification-override",
        _RUNTIME | _CONFIG,
        choices=("bypass-all", "bypass-catalog", "bypass-signature"),
        metavar="MODE",
        no_effect=_CONFIG | {"cookie"},
    ),
    OptionDefinition("json", _ALL, default=False, dest="json_output", flag=True),
    OptionDefinition("plugin-config", _OVERRIDES, repeat=True, metavar="ID=JSON"),
    OptionDefinition("plugin-config-file", _OVERRIDES, Path, repeat=True, metavar="ABSOLUTE_PATH"),
    OptionDefinition("fallback-generic", _OVERRIDES, default="auto", choices=("auto", "enabled", "disabled")),
    OptionDefinition("no-console-log", _FORMAT, default=False, flag=True),
    OptionDefinition("list-updated-urls", frozenset({"download", "download --inspect-only"}), default=False, flag=True),
    OptionDefinition("inspect-only", frozenset({"download", "download --inspect-only"}), default=False, flag=True),
    OptionDefinition("existing-file", _FORMAT, choices=("overwrite", "skip", "rename", "error")),
    OptionDefinition("image-format", _FORMAT, choices=_IMAGE_FORMATS),
    OptionDefinition("force-image-format", _DOWNLOAD, choices=_IMAGE_FORMATS),
    OptionDefinition(
        "output-dir", _DOWNLOAD | {"download --inspect-only"}, Path, metavar="ABSOLUTE_PATH", no_effect=_OUTPUT_NOOP
    ),
    OptionDefinition(
        "directory-format", _DOWNLOAD | {"download --inspect-only"}, metavar="FORMAT", no_effect=_OUTPUT_NOOP
    ),
    OptionDefinition(
        "plugin",
        _DOWNLOAD | _INSPECT | {"state workflow list", "state workflow show", "state workflow history"},
        dest="plugin_id",
        metavar="ID",
    ),
    OptionDefinition("force-plugin", _DOWNLOAD | _INSPECT, dest="force_plugin_id", metavar="ID"),
    OptionDefinition("plugin-download-policy", _DOWNLOAD, repeat=True, metavar="ID=JSON"),
    OptionDefinition("plugin-download-policy-file", _DOWNLOAD, Path, repeat=True, metavar="ABSOLUTE_PATH"),
    OptionDefinition(
        "inspection-data", _INSPECT, default=argparse.SUPPRESS, choices=("url", "http", "all"), metavar="LEVEL"
    ),
    OptionDefinition("manifest-only", _INSPECT, default=False, flag=True),
    OptionDefinition(
        "selection-priority",
        frozenset({"plugin install", "plugin trust"}),
        int,
        default=0,
        help="plugin install/trust priority (default: 0)",
    ),
    OptionDefinition("host", frozenset({"doctor", "config explain"}), help="bare host or absolute HTTP(S) URL"),
    OptionDefinition("export-cookies", frozenset({"cookie"}), Path, help="legacy Cookie export"),
    OptionDefinition("import-cookies", frozenset({"cookie"}), Path, help="legacy Cookie import"),
    OptionDefinition(
        "import-browser-cookies", frozenset({"cookie"}), metavar="DOMAIN", help="legacy browser Cookie import"
    ),
    OptionDefinition("limit", frozenset({"state workflow history"}), int, help="history limit (default: 20)"),
    OptionDefinition(
        "dry-run",
        frozenset({"workflow", "state workflow prune"}),
        default=False,
        flag=True,
        help="preview workflow URL selection or history pruning",
    ),
    OptionDefinition("download-scope", frozenset({"workflow"}), choices=("all", "updated")),
    OptionDefinition("workflow-retries", frozenset({"workflow"}), int, help="extra workflow rounds (default: 1)"),
    OptionDefinition(
        "workflow-retry-delay", frozenset({"workflow"}), float, help="seconds before each retry round (default: 600)"
    ),
    OptionDefinition(
        "workflow-retry-timeout",
        frozenset({"workflow"}),
        float,
        help="positive retry duration including waits (default: unlimited)",
    ),
)

# Conditional exclusions (fallback mode and explicit/legacy Cookie command) are
# evaluated by common validation using the same canonical option names.
CONFLICT_GROUPS = (
    ("image-format", "force-image-format"),
    ("plugin", "force-plugin"),
    ("inspect-only", "list-updated-urls"),
    ("force-image-format", "list-updated-urls"),
    ("export-cookies", "import-cookies", "import-browser-cookies"),
)
LEGACY_COOKIE_OPTIONS = frozenset(CONFLICT_GROUPS[-1])
