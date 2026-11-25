"""Static command meanings and examples; never inspect user files to show help."""

from .._cli_help import CommandHelp

PROGRAM = "image-downloader"
PAGE = "https://example.test/gallery"
FEED = "https://example.test/feed"
RUN = "12345678-1234-4234-8234-123456789abc"
FILE = '"C:\\Downloads\\cookies.enc"'
SOURCE = '"C:\\Plugin Sources\\gallery"'
PLUGIN = "com.example.gallery"

URL = (("URL", "Absolute HTTP(S) URL of the page to process."),)
WORKFLOW_URL = (("WORKFLOW_URL", "Original workflow input URL, rather than a returned download target URL."),)
TARGET_URL = (("TARGET_URL", "Exact download target URL returned by a plugin, rather than the workflow input URL."),)


def command(
    summary: str,
    invocation: str,
    arguments: tuple[tuple[str, str], ...] = (),
    notes: tuple[str, ...] = (),
    variant: str = "--json",
    related: tuple[str, ...] = (),
) -> CommandHelp:
    base = f"{PROGRAM} {invocation}"
    return CommandHelp(summary, (base, f"{base} {variant}"), arguments, notes, related)


ROOT_GROUPS = (
    ("Download and inspect", ("download", "workflow", "inspect")),
    ("State and verification", ("state", "verify")),
    ("Configuration and diagnostics", ("config", "doctor")),
    ("Plugins and credentials", ("plugin", "cookie", "credential", "vault")),
)

COMMANDS = {
    "": CommandHelp(
        "Download images with local API v3 plugins, inspect requests, and manage saved state.",
        (f"{PROGRAM} download {PAGE}", f"{PROGRAM} workflow {FEED} --dry-run", f"{PROGRAM} help download"),
    ),
    "download": command(
        "Download images from a page using a matching site plugin.",
        f"download {PAGE}",
        URL,
        ("The bare-URL form is equivalent to download URL. Output overrides apply only to this invocation.",),
        '--output-dir "C:\\Downloads"',
        ("inspect", "workflow"),
    ),
    "download --inspect-only": command(
        "Inspect the manifest and image requests without downloading image bodies; alias of inspect.",
        f"download {PAGE} --inspect-only",
        URL,
        ("Manifest discovery and authentication may use the network. Request previews may contain secrets.",),
        "--manifest-only",
        ("inspect",),
    ),
    "download --list-updated-urls": command(
        "List added or changed download URLs and save the independent update snapshot; do not download images.",
        f"download {FEED} --list-updated-urls",
        URL,
        ("This operation updates its saved snapshot. Its history is independent of workflow completion state.",),
        related=("workflow",),
    ),
    "workflow": command(
        "Check a workflow input URL, select targets, download them, and retry unfinished work.",
        f"workflow {FEED}",
        URL,
        (
            "--dry-run performs one real update check, which may use authentication and network access. It does not "
            "save workflow state or download images; output settings and retry rounds are not simulated.",
        ),
        "--dry-run",
        ("state workflow", "verify workflow"),
    ),
    "inspect": command(
        "Inspect a manifest and resolved image requests without downloading image bodies.",
        f"inspect {PAGE}",
        URL,
        (
            "--inspection-data changes displayed fields only; --manifest-only skips image-request resolution. "
            "Manifest discovery and authentication may use the network. Previews may contain secrets.",
        ),
        "--manifest-only",
        ("download",),
    ),
    "doctor": command(
        "Diagnose effective configuration, plugin loading, and plugin selection.",
        "doctor",
        variant="--host example.test",
        related=("config explain", "plugin list"),
    ),
    "config": command("Locate, explain, or initialize application and profile configuration.", "config path"),
    "config path": command("Show standard configuration locations and automatic storage roots.", "config path"),
    "config explain": command(
        "Show effective configuration, configuration layers, and where each value originated.",
        "config explain",
        variant="--host example.test",
        related=("doctor",),
    ),
    "config init": command(
        "Create a configuration template; an existing file is never overwritten.",
        "config init",
        (("PATH", "Absolute destination file path; omitted: the fixed user configuration path."),),
        variant='--data-root "C:\\Image Data"',
    ),
    "config profile": command("Create profile-specific configuration overrides.", "config profile init work"),
    "config profile init": command(
        "Create an empty profile configuration, creating the main configuration if necessary.",
        "config profile init work",
        (("NAME", "Profile name using letters, digits, underscores, or hyphens."),),
    ),
    "plugin": command("List, install, trust, revoke, or uninstall local plugins.", "plugin list"),
    "plugin list": command("List catalog registrations and plugin loading diagnostics.", "plugin list"),
    "plugin install": command(
        "Verify, copy, and trust a plugin source directory in the configured plugin root.",
        f"plugin install {SOURCE}",
        (("SOURCE_DIR", "Existing absolute plugin source directory."),),
        variant="--yes",
    ),
    "plugin trust": command(
        "Verify a plugin source and register its trust without copying the source directory.",
        f"plugin trust {SOURCE}",
        (("SOURCE_DIR", "Existing absolute plugin source directory."),),
        variant="--selection-priority 10",
    ),
    "plugin revoke": command(
        "Revoke a plugin catalog registration while keeping installed files.",
        f"plugin revoke {PLUGIN}",
        (("ID", "Registered plugin identifier."),),
        variant="--yes",
    ),
    "plugin uninstall": command(
        "Remove an installed plugin and its catalog registration.",
        f"plugin uninstall {PLUGIN}",
        (("ID", "Installed plugin identifier."),),
        variant="--yes",
    ),
    "cookie": command("Export, import, or copy browser cookies for the selected profile.", f"cookie export {FILE}"),
    "cookie export": command(
        "Export the selected profile's cookies to an encrypted file; prompts for a passphrase.",
        f"cookie export {FILE}",
        (("FILE", "Destination encrypted cookie file."),),
    ),
    "cookie import": command(
        "Import an encrypted cookie file into the selected profile; prompts for a passphrase.",
        f"cookie import {FILE}",
        (("FILE", "Encrypted cookie file to read."),),
    ),
    "cookie browser-import": command(
        "Copy cookies for a domain from a local browser into the selected profile.",
        "cookie browser-import example.test",
        (("DOMAIN", "Cookie domain to import."),),
        variant='--chrome-user-data-dir "C:\\Browser Data"',
    ),
    "state": command(
        "View workflow registrations, inspect saved runs, or change target statuses.", "state workflow list"
    ),
    "state workflow": command(
        "Manage saved workflow state and execution history. Omitting the subcommand is equivalent to list.",
        "state workflow list",
        related=("workflow",),
    ),
    "state workflow list": command(
        "List saved workflow registrations and completion summaries.", "state workflow list"
    ),
    "state workflow show": command(
        "Show saved state for an original workflow input URL.",
        f"state workflow show {FEED}",
        WORKFLOW_URL,
    ),
    "state workflow history": command(
        "List saved workflow runs, optionally filtered by the original input URL.",
        "state workflow history",
        WORKFLOW_URL,
        variant="--limit 5",
        related=("state workflow run",),
    ),
    "state workflow run": command(
        "Show details of one saved workflow run.",
        f"state workflow run {RUN}",
        (("RUN_ID", "Identifier of a saved workflow execution; this operation only displays its record."),),
        related=("state workflow history", "verify workflow"),
    ),
    "state workflow prune": command(
        "Prune saved execution history according to configured retention and size limits.",
        "state workflow prune",
        variant="--dry-run",
    ),
    "state workflow targets": command(
        "List download target URLs and their statuses, using saved registrations or a live update query.",
        "state workflow targets",
        TARGET_URL,
        variant=f"--filter-workflow-url {FEED}",
    ),
    "state workflow set": command(
        "Change approved target URLs to the requested status.",
        f"state workflow set {PAGE} --status completed",
        TARGET_URL,
        variant="--dry-run",
    ),
    "state workflow reset": command(
        "Change approved target URLs to pending so they can be selected by a later workflow.",
        f"state workflow reset {PAGE}",
        TARGET_URL,
        variant="--dry-run",
    ),
    "verify": command(
        "Check recorded log errors or match a saved workflow run to output files.", 'verify logs "C:\\Downloads"'
    ),
    "verify logs": command(
        "Check saved logs for recorded errors, incomplete sections, and indeterminate records.",
        'verify logs "C:\\Downloads"',
        (("PATH", "Absolute directory, or a single log.log or debug.log file."),),
        ("This checks recorded evidence, not image decoding or content integrity. No downloads are performed.",),
    ),
    "verify workflow": command(
        "Check a saved run's selected URLs, expected files, and chapter logs under an output directory.",
        f'verify workflow {RUN} --output-dir "C:\\Downloads"',
        (("RUN_ID", "Canonical UUID of a saved workflow run."),),
        ("--output-dir is required. File existence and size are checked; image contents are not decoded.",),
    ),
    "credential": command(
        "Manage plugin and SMTP credentials without displaying secret values.", "credential plugin list"
    ),
    "vault": command(
        "Manage the data-root-wide credential vault, backups, and encryption keys.",
        "vault status",
        notes=("Vault operations cover the entire data root and do not accept --profile.",),
    ),
}

for _kind in ("plugin", "smtp"):
    COMMANDS[f"credential {_kind}"] = command(
        f"Manage {_kind} credentials in the selected profile.", f"credential {_kind} list"
    )
    for _action in ("set", "list", "delete"):
        _arguments = (
            ()
            if _action == "list"
            else (
                ((("PLUGIN_ID", "Plugin identifier owning the credential."),) if _kind == "plugin" else ())
                + (("REF", "Credential reference name."),)
            )
        )
        _operand = "" if _action == "list" else (f" {PLUGIN}" if _kind == "plugin" else "") + " account"
        _summary = {
            "set": f"Store {_kind} credentials using hidden input or explicit standard input.",
            "list": f"List {_kind} credential references without revealing their values.",
            "delete": f"Delete a {_kind} credential reference after confirmation.",
        }[_action]
        COMMANDS[f"credential {_kind} {_action}"] = command(
            _summary,
            f"credential {_kind} {_action}{_operand}",
            _arguments,
            variant="--yes" if _action == "delete" else "--json",
        )

for _action, _summary in (
    ("init", "Initialize the credential vault and its master key."),
    ("status", "Show vault readiness and key-rotation state without initializing it."),
    ("backup", "Create a passphrase-encrypted backup of the credential vault."),
    ("restore", "Restore the credential vault from a passphrase-encrypted backup."),
    ("rotate-key", "Rotate the vault master key or resume an interrupted rotation."),
):
    _has_file = _action in {"backup", "restore"}
    COMMANDS[f"vault {_action}"] = command(
        _summary,
        f"vault {_action}" + (' "C:\\Backups\\vault.enc"' if _has_file else ""),
        (("FILE", "Absolute backup file path."),) if _has_file else (),
        ("This operation covers the entire data root; --profile is not accepted.",),
    )
