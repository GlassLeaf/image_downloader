"""Presentation metadata; acceptance, types and choices live in options.py."""

GROUPS = (
    "Operation mode",
    "Configuration and profile",
    "Plugins",
    "Output location and naming",
    "Output setting overrides",
    "Inspection",
    "Workflow and retries",
    "Target selection and status",
    "Display and limits",
    "Preview and confirmation",
    "Credential input and recovery",
    "Browser cookie source",
)

# name: (purpose group, meaning, behavior when omitted).
OPTION_HELP = {
    "host": (
        "Configuration and profile",
        "Resolve site-specific settings for a bare host or absolute HTTP(S) URL.",
        "no site-specific target",
    ),
    "config": (
        "Configuration and profile",
        "Read application settings from this absolute file path.",
        "uses the fixed user configuration, or bundled settings if absent",
    ),
    "profile": (
        "Configuration and profile",
        "Select the configuration and data profile by name.",
        "Uses configuration",
    ),
    "data-root": (
        "Configuration and profile",
        "Use this absolute directory as the data root for this invocation.",
        "Uses configuration",
    ),
    "plugin-root": ("Plugins", "Discover or manage plugins under this absolute directory.", "Uses configuration"),
    "plugin": ("Plugins", "Select a site plugin by ID; its URL matcher must accept the URL.", "automatic selection"),
    "force-plugin": (
        "Plugins",
        "Select an enabled external site plugin by ID, bypassing its URL matcher.",
        "automatic selection",
    ),
    "plugin-verification-override": (
        "Plugins",
        "Override plugin verification for this invocation only.",
        "Uses configuration",
    ),
    "plugin-config": ("Plugins", "Override plugin settings using ID=<JSON-object>; inline values override files.", ""),
    "plugin-config-file": ("Plugins", "Read plugin settings from an absolute JSON file with plugin_id and config.", ""),
    "plugin-download-policy": (
        "Plugins",
        "Override download policy using ID=<JSON-object>; inline values override files.",
        "",
    ),
    "plugin-download-policy-file": ("Plugins", "Read an absolute JSON file with plugin_id and download_policy.", ""),
    "fallback-generic": ("Plugins", "Control generic HTML fallback; auto preserves the configured setting.", ""),
    "selection-priority": ("Plugins", "Set plugin selection priority as an integer during registration.", ""),
    "inspect-only": (
        "Operation mode",
        "Inspect the manifest and image requests instead of downloading; alias of inspect.",
        "",
    ),
    "list-updated-urls": ("Operation mode", "List added or changed URLs and save the independent update snapshot.", ""),
    "output-dir": (
        "Output location and naming",
        "Save downloads under this absolute directory for this invocation.",
        "Uses configuration",
    ),
    "directory-format": (
        "Output location and naming",
        "Override the chapter directory naming template.",
        "Uses configuration",
    ),
    "existing-file": (
        "Output setting overrides",
        "Choose how to handle an existing output file.",
        "Uses configuration",
    ),
    "image-format": (
        "Output setting overrides",
        "Set the default output format; per-image plugin settings take priority.",
        "Uses configuration",
    ),
    "force-image-format": (
        "Output setting overrides",
        "Override even per-image plugin formats. ORIGINAL saves the final "
        "plugin-produced bytes without core re-encoding.",
        "no forced format",
    ),
    "inspection-data": (
        "Inspection",
        "Choose displayed inspection fields only; this does not reduce request resolution. "
        "http and all may expose credentials and request bodies.",
        "url",
    ),
    "manifest-only": ("Inspection", "Inspect only the manifest; do not resolve individual image requests.", ""),
    "download-scope": (
        "Workflow and retries",
        "Select updated/uncompleted targets or all current candidates.",
        "updated",
    ),
    "workflow-progress-log": (
        "Workflow and retries",
        "Enable or disable incremental workflow logs; the acquisition plan is still saved before downloads.",
        "Uses configuration",
    ),
    "workflow-retries": (
        "Workflow and retries",
        "Number of extra workflow rounds; an integer greater than or equal to 0.",
        "1",
    ),
    "workflow-retry-delay": (
        "Workflow and retries",
        "Seconds to wait before each retry round; finite and non-negative.",
        "600 seconds",
    ),
    "workflow-retry-timeout": (
        "Workflow and retries",
        "Maximum retry duration including waits, in finite positive seconds.",
        "unlimited",
    ),
    "url-regex": (
        "Target selection and status",
        "Match returned download target URLs with a case-sensitive regex.",
        "no regex filter",
    ),
    "all": ("Target selection and status", "Select all matching target URLs; does not change --download-scope.", ""),
    "filter-workflow-url": (
        "Target selection and status",
        "Filter saved registrations by the original workflow input URL; does not execute plugins.",
        "no workflow URL filter",
    ),
    "query-targets-from-workflow-url": (
        "Target selection and status",
        "Run the plugin update check using this workflow "
        "input URL; may use authentication and network access, but downloads no images. "
        "Querying alone does not save state.",
        "use saved registrations",
    ),
    "from-status": ("Target selection and status", "Filter targets by their current status.", "all statuses"),
    "status": ("Target selection and status", "Set the status of approved targets.", "required for set"),
    "json": ("Display and limits", "Write one structured JSON result; help remains plain text.", ""),
    "no-console-log": (
        "Display and limits",
        "Disable console diagnostic logging for this invocation.",
        "Uses configuration",
    ),
    "limit": ("Display and limits", "Maximum number of saved runs to display; a positive integer.", "20"),
    "dry-run": ("Preview and confirmation", "Preview planned actions without applying them.", ""),
    "yes": ("Preview and confirmation", "Skip the confirmation prompt for this change.", "confirmation required"),
    "stdin": (
        "Credential input and recovery",
        "Read the secret from standard input. SMTP expects a JSON object with "
        "username and password; plugin credentials expect a UTF-8 secret.",
        "hidden interactive input",
    ),
    "passphrase-stdin": (
        "Credential input and recovery",
        "Read the backup passphrase from standard input.",
        "hidden interactive input",
    ),
    "resume": ("Credential input and recovery", "Resume an interrupted key rotation.", "start a new key rotation"),
    "chrome-user-data-dir": (
        "Browser cookie source",
        "Read the Chrome Default profile from this existing absolute "
        "user-data directory. Only accepted by cookie browser-import.",
        "automatic browser selection",
    ),
    "export-cookies": (
        "Legacy invocation forms",
        "Export encrypted cookies; use cookie export for new invocations.",
        "required for this legacy invocation",
    ),
    "import-cookies": (
        "Legacy invocation forms",
        "Import encrypted cookies; use cookie import for new invocations.",
        "required for this legacy invocation",
    ),
    "import-browser-cookies": (
        "Legacy invocation forms",
        "Import browser cookies; use cookie browser-import instead.",
        "required for this legacy invocation",
    ),
}
