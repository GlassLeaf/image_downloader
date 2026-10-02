"""Investigate CLI messages without changing application behavior.

Run with the repository's Python environment. Results are diagnostic artifacts,
not tests or a specification for a future CLI implementation.
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import io
import itertools
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))


class ValidationComplete(BaseException):
    """Stop a cookie command before accessing cookies or asking for a secret."""


def main() -> None:  # noqa: C901 - Keep the investigation cases and their instrumentation in one replayable script.
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=Path, default=REPO / "docs/investigations/cli-arguments-2026-10-02")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--shard-index", type=int, default=0)
    ap.add_argument("--shard-count", type=int, default=1)
    ap.add_argument("--only-additional", action="store_true")
    ap.add_argument("--reuse-parser", action="store_true", help="reuse the public parser with a new Namespace per case")
    ap.add_argument("--replay", type=Path, help="replay the preserved raw-results.json case definitions")
    ap.add_argument(
        "--execution-boundary", action="store_true", help="resolve config without writes, then stop before handlers"
    )
    ap.add_argument(
        "--valid-url-probes", action="store_true", help="replay invalid URL operands with an absolute HTTP(S) URL"
    )
    options = ap.parse_args()
    if options.shard_count < 1 or not 0 <= options.shard_index < options.shard_count:
        ap.error("shard-index must be between zero and shard-count minus one")
    output = options.output.resolve()
    if options.replay is not None and output == options.replay.resolve().parent:
        ap.error("replay output must be separate from the preserved investigation")
    if options.valid_url_probes and not options.execution_boundary:
        ap.error("valid URL probes require --execution-boundary")
    output.mkdir(parents=True, exist_ok=True)
    audit_root = REPO / ".runtime/cli-argument-audit"
    audit_root.mkdir(parents=True, exist_ok=True)
    root = audit_root / f"run-{time.time_ns()}"
    root.mkdir()
    # Match the program name used by `python -m image_downloader` in usage text.
    sys.argv[0] = str(REPO / "src/image_downloader/__main__.py")
    # platformdirs exposes these Windows overrides; no application path functions
    # are replaced. A module subprocess receives exactly the same environment.
    os.environ["WIN_PD_OVERRIDE_LOCAL_APPDATA"] = str(root / "local")
    os.environ["WIN_PD_OVERRIDE_APPDATA"] = str(root / "roaming")
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["PYTHONPATH"] = str(REPO / "src")

    from image_downloader.commands import cookie, dispatch, parser, setup, workflow
    from image_downloader.commands.config import _explain_site
    from image_downloader.configuration.paths import default_user_config_path

    user_config = default_user_config_path()
    assert user_config.is_relative_to(root)
    user_config.parent.mkdir(parents=True, exist_ok=True)
    data_root = root / "data"
    plugin_root = root / "plugins"
    plugin_root.mkdir(exist_ok=True)
    # Bare invalid URL operands prevent fallback from issuing HTTP requests.
    fixture = json.dumps({"storage": {"data_root": str(data_root)}, "plugins": {"root": str(plugin_root)}})
    user_config.write_text(fixture, encoding="utf-8")
    valid_config = root / "valid.yaml"
    valid_config.write_text(fixture, encoding="utf-8")
    (user_config.parent / "profiles/existing").mkdir(parents=True, exist_ok=True)
    (user_config.parent / "profiles/existing/app.yaml").write_text("{}", encoding="utf-8")
    files = {
        "bad-yaml.yaml": "[invalid",
        "non-mapping.yaml": "[]",
        "bad-field.yaml": "network: {request_concurrency: 0}",
        "unknown-field.yaml": "unknown_key: true",
        "bad-profile.yaml": "profile: {default: '../escape'}",
        "bad-json.json": "{",
        "wrong-config-schema.json": '{"config": {}}',
        "plugin-config.json": '{"plugin_id": "core.generic-html", "config": {}}',
        "plugin-policy.json": '{"plugin_id": "core.generic-html", "download_policy": {"request_concurrency": 1}}',
        "non-mapping.json": "[]",
    }
    for name, content in files.items():
        (root / name).write_text(content, encoding="utf-8")

    values = {
        "@CONFIG@": str(valid_config),
        "@DATA@": str(data_root),
        "@PLUGINS@": str(plugin_root),
        "@OUTPUT@": str(root / "output"),
        "@MISSING@": str(root / "missing"),
        "@USER_CONFIG@": str(user_config),
    }
    for name in files:
        values["@" + name + "@"] = str(root / name)

    specs = {
        "--config": ["@CONFIG@"],
        "--profile": ["default"],
        "--data-root": ["@DATA@"],
        "--plugin-root": ["@PLUGINS@"],
        "--yes": [],
        "--json": [],
        "--plugin-verification-override": ["bypass-signature"],
        "--plugin-config": ["core.generic-html={}"],
        "--plugin-config-file": ["@plugin-config.json@"],
        "--fallback-generic": ["auto"],
        "--no-console-log": [],
        "--list-updated-urls": [],
        "--inspect-only": [],
        "--existing-file": ["skip"],
        "--image-format": ["PNG"],
        "--force-image-format": ["JPEG"],
        "--output-dir": ["@OUTPUT@"],
        "--directory-format": ["%CHAPTER_NUMBER%"],
        "--plugin": ["com.example.missing"],
        "--force-plugin": ["com.example.missing"],
        "--plugin-download-policy": ["core.generic-html={}"],
        "--plugin-download-policy-file": ["@plugin-policy.json@"],
        "--inspection-data": ["http"],
        "--manifest-only": [],
        "--selection-priority": ["1"],
        "--host": ["example.test"],
        "--export-cookies": ["@MISSING@"],
        "--import-cookies": ["@MISSING@"],
        "--import-browser-cookies": ["example.test"],
        "--download-scope": ["all"],
        "--workflow-retries": ["0"],
        "--workflow-retry-delay": ["0"],
        "--workflow-retry-timeout": ["1"],
        "--limit": ["1"],
        "--dry-run": [],
    }
    commands = {
        "download": ["download", "probe-invalid-url"],
        "workflow": ["workflow", "probe-invalid-url"],
        "inspect": ["inspect", "probe-invalid-url"],
        "doctor": ["doctor"],
        "config/path": ["config", "path"],
        "config/explain": ["config", "explain"],
        "config/init": ["config", "init", "relative.yaml"],
        "config/profile": ["config", "profile", "init", "invalid/name"],
        "plugin/list": ["plugin", "list"],
        "plugin/install": ["plugin", "install", "@MISSING@"],
        "plugin/trust": ["plugin", "trust", "@MISSING@"],
        "plugin/revoke": ["plugin", "revoke", "com.example.missing"],
        "plugin/uninstall": ["plugin", "uninstall", "com.example.missing"],
        "cookie/export": ["cookie", "export", "@MISSING@"],
        "cookie/import": ["cookie", "import", "@MISSING@"],
        "cookie/browser-import": ["cookie", "browser-import", "example.test"],
        "state/list": ["state", "workflow", "list"],
        "state/show": ["state", "workflow", "show", "https://example.test/feed"],
        "state/history": ["state", "workflow", "history"],
        "state/run": ["state", "workflow", "run", "missing-run"],
        "state/prune": ["state", "workflow", "prune"],
    }
    cases: dict[tuple[str, ...], dict[str, object]] = {}

    def add(words, family, expectation=None, note=""):
        key = tuple(words)
        if key not in cases:
            cases[key] = {"argv": list(key), "family": family, "expected_hint": expectation, "note": note}

    for words in [
        [],
        ["--list"],
        ["--unknown-option"],
        ["help"],
        ["help", "plugin"],
        ["plugin", "help"],
        ["config", "help"],
        ["state", "help"],
        ["cookie", "help"],
        ["--help"],
        ["-h"],
        ["--version"],
        ["version"],
        ["unknown-command"],
        ["list"],
        ["download"],
        ["workflow"],
        ["inspect"],
        ["plugin"],
        ["config"],
        ["cookie"],
        ["state"],
        ["probe-invalid-url"],
        ["probe-invalid-url", "plugin"],
    ]:
        hint = None
        if words and words[0] in {"help", "version", "unknown-command", "list"}:
            hint = (
                f"引数エラー: 未定義のコマンド／HTTP(S) URLではない値 '{words[0]}'（help は --help の別名にする案も可）"
            )
        if not words:
            hint = "引数エラー: URLまたはコマンドが必要です。--help を参照してください"
        add(words, "examples-and-basics", hint)

    for base in commands.values():
        add(base, "command-baseline")
        add([base[0]], "missing-operands")
        add([*base, "extra", "extra2"], "excess-operands")
        for flag, vals in specs.items():
            option_words = [flag, *vals]
            add([*option_words, *base], "option-before-command")
            add([*base, *option_words], "option-after-operands")
            add([base[0], *option_words, *base[1:]], "option-between-command-and-operands")
        for unknown in ["--unknown-option", "--list", "--version", "--JSON", "-x"]:
            add([unknown, *base], "unknown-option-before")
            add([*base, unknown], "unknown-option-after")
        for position in range(len(base) + 1):
            add([*base[:position], "--help", *base[position:]], "help-position")
            add([*base[:position], "--unknown-option", *base[position:]], "unknown-option-position")

    value_options = {name: vals for name, vals in specs.items() if vals}
    for flag, vals in value_options.items():
        add([flag], "missing-value")
        add(["doctor", flag], "missing-value-after-command")
        add([flag + "="], "empty-equals-value")
        add([flag, ""], "empty-value")
        add([flag, "--unknown-option"], "value-followed-by-unknown")
        add([flag, "--help"], "value-followed-by-help")
        add([flag, "doctor"], "command-consumed-as-option-value")
        add([flag + "=" + vals[0], "doctor"], "equals-value")
        add([flag, vals[0], flag, vals[0], "doctor"], "duplicate-option")

    invalid = {
        "--existing-file": ["invalid", "SKIP", ""],
        "--image-format": ["png", "GIF", "invalid"],
        "--force-image-format": ["png", "GIF"],
        "--inspection-data": ["private", "URL"],
        "--fallback-generic": ["true", "invalid"],
        "--plugin-verification-override": ["strict", "warn", "off", "invalid"],
        "--download-scope": ["invalid", "ALL"],
        "--workflow-retries": ["abc", "1.5", "-1"],
        "--workflow-retry-delay": ["abc", "-1", "nan", "inf"],
        "--workflow-retry-timeout": ["abc", "-1", "0", "nan", "inf"],
        "--selection-priority": ["abc", "1.5", "-1", "0"],
        "--limit": ["abc", "0", "-1"],
        "--config": [
            "relative.yaml",
            "@MISSING@",
            "@bad-yaml.yaml@",
            "@non-mapping.yaml@",
            "@bad-field.yaml@",
            "@unknown-field.yaml@",
            "@bad-profile.yaml@",
        ],
        "--data-root": ["relative", ""],
        "--plugin-root": ["relative", ""],
        "--output-dir": ["relative", ""],
        "--directory-format": ["%NUM%", "../escape", ""],
        "--profile": ["../escape", "", "missing-profile"],
        "--host": ["file:///tmp/x", "http://", "bad host"],
        "--plugin-config": ["missing-equals", "id={", "id=[]", "id=null", "={}", "id=1"],
        "--plugin-download-policy": ["missing-equals", "id={", "id=[]", 'id={"request_concurrency": 0}'],
        "--plugin-config-file": [
            "relative.json",
            "@MISSING@",
            "@bad-json.json@",
            "@wrong-config-schema.json@",
            "@non-mapping.json@",
        ],
        "--plugin-download-policy-file": ["relative.json", "@MISSING@", "@bad-json.json@", "@plugin-config.json@"],
    }
    for flag, vals in invalid.items():
        base = (
            commands["workflow"]
            if flag.startswith("--workflow-") or flag == "--download-scope"
            else commands["download"]
        )
        if flag == "--host":
            base = commands["doctor"]
        if flag == "--limit":
            base = commands["state/history"]
        for value in vals:
            add([flag, value, *base], "invalid-value-before")
            add([*base, flag, value], "invalid-value-after")

    # Every unordered option pair is exercised in both orders. General pairs
    # precede download; the important conflicts additionally span parser levels.
    pair_specs = {k: v for k, v in specs.items() if k != "--json"}
    for (left, lv), (right, rv) in itertools.combinations(pair_specs.items(), 2):
        add([left, *lv, right, *rv, *commands["download"]], "all-option-pairs-left-right")
        add([right, *rv, left, *lv, *commands["download"]], "all-option-pairs-right-left")
    conflicts = [
        (["--image-format", "PNG"], ["--force-image-format", "JPEG"]),
        (["--plugin", "com.example.missing"], ["--force-plugin", "com.example.missing"]),
        (["--inspect-only"], ["--list-updated-urls"]),
        (["--plugin", "com.example.missing"], ["--fallback-generic", "disabled"]),
        (["--force-plugin", "com.example.missing"], ["--fallback-generic", "enabled"]),
        (["--force-image-format", "PNG"], ["--list-updated-urls"]),
        (["--export-cookies", "@MISSING@"], ["--import-cookies", "@MISSING@"]),
        (["--export-cookies", "@MISSING@"], ["--import-browser-cookies", "example.test"]),
    ]
    for base in [commands["download"], commands["workflow"], commands["inspect"], []]:
        for left, right in conflicts:
            for first, second in [(left, right), (right, left)]:
                add([*first, *second, *base], "conflict-same-parser-before")
                add([*base, *first, *second], "conflict-same-parser-after")
                add([*first, *base, *second], "conflict-across-parser-levels")

    for words in [
        ["config", "unknown"],
        ["config", "path", "extra"],
        ["config", "profile"],
        ["config", "profile", "unknown"],
        ["config", "profile", "init"],
        ["config", "profile", "init", "existing"],
        ["config", "init", "@USER_CONFIG@"],
        ["config", "init", "@NEW_CONFIG@"],
        ["config", "profile", "init", "@NEW_PROFILE@"],
        ["plugin", "unknown"],
        ["plugin", "list", "extra"],
        ["plugin", "install"],
        ["plugin", "trust"],
        ["plugin", "revoke"],
        ["plugin", "uninstall"],
        ["plugin", "install", "relative"],
        ["plugin", "trust", "relative"],
        ["cookie", "unknown"],
        ["cookie", "import"],
        ["cookie", "export"],
        ["cookie", "browser-import"],
        ["state", "unknown"],
        ["state", "workflow"],
        ["state", "workflow", "unknown"],
        ["state", "workflow", "show"],
        ["state", "workflow", "run"],
        ["state", "workflow", "list", "extra"],
        ["state", "workflow", "prune", "extra"],
        ["state", "workflow", "run", "a", "b"],
        ["state", "workflow", "history", "a", "b"],
    ]:
        add(words, "subcommand-validation")

    for prefix in ["--lis", "--conf", "--pro", "--plugin-c", "--image-f", "--j", "--he", "--workflow-retry"]:
        add([prefix], "option-abbreviation")
        add([prefix, "dummy", "doctor"], "option-abbreviation-with-value")
    for words in [
        ["--", "help"],
        ["--", "probe-invalid-url"],
        ["download", "--", "--unknown-option"],
        ["download", "--", "probe-invalid-url"],
        ["--help", "--unknown-option"],
        ["--unknown-option", "--help"],
        ["--config", "--help"],
        ["plugin", "--unknown-option", "--help"],
        ["plugin", "--help", "--unknown-option"],
        ["--json=true"],
        ["--yes=false"],
        ["--dry-run=false"],
        ["--profile", "plugin"],
        ["--profile=plugin"],
        ["--plugin", "com.example.missing", "download", "probe-invalid-url", "--plugin", "other.id"],
        [
            "--plugin-config",
            "core.generic-html={}",
            "download",
            "probe-invalid-url",
            "--plugin-config",
            'core.generic-html={"x":1}',
        ],
    ]:
        add(words, "tokenization-and-priority")
    # Options/defaults that are silently tolerated, and JSON's exact-token check.
    for base in [
        commands["doctor"],
        commands["plugin/list"],
        commands["config/path"],
        commands["state/list"],
        commands["cookie/import"],
    ]:
        for flag, value in [("--selection-priority", "0"), ("--fallback-generic", "auto")]:
            add([flag, value, *base], "explicit-default-value")
        add(["--inspect-only", *base], "unexpectedly-ignored-flag")
        add(["--manifest-only", *base], "unexpectedly-ignored-flag")

    originals = list(cases.values())
    for case in originals:
        if "--json" not in case["argv"]:
            add(["--json", *case["argv"]], case["family"] + "/json-before", case["expected_hint"], case["note"])
            # JSON after operands matters particularly for parsing/error priority.
            if case["family"] in {
                "examples-and-basics",
                "help-position",
                "missing-value",
                "subcommand-validation",
                "conflict-across-parser-levels",
            }:
                add([*case["argv"], "--json"], case["family"] + "/json-after", case["expected_hint"], case["note"])

    additional = [
        ["--config", "relative.yaml", "doctor"],
        ["--j", "--unknown-option"],
        ["config", "profile", "--json", "init", "invalid/name"],
        ["config", "profile", "init", "--json", "invalid/name"],
        ["config", "--json", "profile", "init", "invalid/name"],
        ["plugin", "install", "--yes", "@MISSING@"],
        ["plugin", "trust", "--yes", "@MISSING@"],
        ["plugin", "revoke", "--yes", "com.example.missing"],
        ["plugin", "uninstall", "--yes", "com.example.missing"],
        ["state", "workflow", "--json", "list"],
        ["state", "workflow", "history", "--limit", "1", "https://example.test/feed"],
        ["cookie", "import", "--profile", "default", "@MISSING@"],
        ["cookie", "export", "--profile", "default", "@MISSING@"],
        ["--image-format", "PNG", "download", "probe-invalid-url", "--image-format", "JPEG"],
        ["--plugin", "first.id", "download", "probe-invalid-url", "--plugin", "second.id"],
        ["--profile", "first", "doctor", "--profile", "default"],
        ["--profile", "default", "doctor", "--profile", "first"],
        [
            "--plugin-config",
            'core.generic-html={"a":1}',
            "download",
            "probe-invalid-url",
            "--plugin-config",
            'core.generic-html={"b":2}',
        ],
        [
            "--plugin-config-file",
            "@plugin-config.json@",
            "download",
            "probe-invalid-url",
            "--plugin-config-file",
            "@plugin-config.json@",
        ],
        ["--unknown-option=download"],
        ["--unknown-option", "plugin"],
        ["--unknown-option", "value", "plugin", "list"],
        ["--fallback-generic", "disabled", "https://example.test/gallery"],
        ["download", "https://example.test/gallery", "--fallback-generic", "disabled"],
        ["inspect", "https://example.test/gallery", "--fallback-generic", "disabled"],
        ["workflow", "https://example.test/gallery", "--fallback-generic", "disabled"],
        ["ftp://example.test/gallery"],
        ["file:///tmp/probe"],
        ["http://"],
    ]
    for words in additional:
        add(words, "additional-order-and-url-probes")
        if "--json" not in words:
            add(["--json", *words], "additional-order-and-url-probes/json")

    inventory = {}
    top_parser = parser.build_parser()
    inventory["root"] = [a.option_strings for a in top_parser._actions if a.option_strings]
    for a in top_parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            for name, child in a.choices.items():
                inventory[name] = [act.option_strings for act in child._actions if act.option_strings]
    declared = {f for flags in inventory["root"] for f in flags} - {"--help", "-h"}
    assert declared == set(specs), (declared - set(specs), set(specs) - declared)
    if options.reuse_parser:
        dispatch.build_parser = lambda: top_parser

    active = {}
    original_render = dispatch._render_error
    original_parser_error = parser._CliArgumentParser.error
    original_run = dispatch.run
    original_cookie_config = cookie._config_for
    original_workflow_handle = workflow.WorkflowCommandHandler._handle

    def render_error(error, **kwargs):
        active["exception_type"] = type(error).__name__
        active["internal_message"] = str(error)
        active["phase"] = "runtime-or-validation"
        if active.get("parser_error"):
            active["phase"] = "parser"
        return original_render(error, **kwargs)

    def parser_error(self, message):
        active["parser_error"] = message
        active["internal_message"] = message
        active["phase"] = "parser"
        return original_parser_error(self, message)

    async def run(parsed):
        active["parsed"] = json.loads(
            json.dumps(
                vars(parsed), default=lambda value: sorted(value) if isinstance(value, frozenset) else str(value)
            )
        )
        return await original_run(parsed)

    def cookie_config(*args, **kwargs):
        original_cookie_config(*args, **kwargs)
        raise ValidationComplete()

    async def workflow_handle(self, args):
        try:
            return await original_workflow_handle(self, args)
        except Exception as exc:
            active["internal_message"] = str(exc)
            active["exception_type"] = type(exc).__name__
            active["phase"] = "workflow-handler"
            raise

    dispatch._render_error = render_error
    parser._CliArgumentParser.error = parser_error
    dispatch.run = run
    cookie._config_for = cookie_config
    workflow.WorkflowCommandHandler._handle = workflow_handle
    if options.execution_boundary:
        from urllib.parse import urlsplit

        class ExecutionBoundary:
            async def handle(self, args):
                command = getattr(args, "command_handler", "cookie")
                if command == "config" and args.command_args[0] in {"path", "init", "profile"}:
                    # These commands have different config creation contracts;
                    # do not execute a mutating helper just to test parsing.
                    raise ValidationComplete()
                site = urlsplit(args.url).hostname if hasattr(args, "url") else None
                if command in {"doctor", "config"}:
                    site = _explain_site(getattr(args, "host", None))
                setup._resolved_config_for(args, site, rewrite_user_layers=False)
                raise ValidationComplete()

        dispatch._COMMAND_HANDLERS = {name: ExecutionBoundary() for name in dispatch._COMMAND_HANDLERS}
    raw_rows = []
    started = time.monotonic()
    all_cases = list(cases.values())
    if options.replay is not None:
        baseline = json.loads(options.replay.read_text(encoding="utf-8"))
        all_cases = [
            {key: row[key] for key in ("id", "argv", "family", "expected_hint", "note") if key in row}
            for row in baseline["cases"]
        ]
    if options.valid_url_probes:
        all_cases = [
            dict(
                case,
                argv=[
                    "https://example.test/audit-feed" if token == "probe-invalid-url" else token
                    for token in case["argv"]
                ],
            )
            for case in all_cases
            if "probe-invalid-url" in case["argv"]
        ]
    if options.limit:
        all_cases = all_cases[: options.limit]
    for index, case in enumerate(all_cases, 1):
        if options.only_additional and not case["family"].startswith("additional"):
            continue
        if (index - 1) % options.shard_count != options.shard_index:
            continue
        active.clear()
        replacements = {**values, "@NEW_CONFIG@": str(root / f"new-{index}.yaml"), "@NEW_PROFILE@": f"new_{index}"}
        argv = [replacements.get(token, token) for token in case["argv"]]
        stdout, stderr = io.StringIO(), io.StringIO()
        original_stdin = sys.stdin
        sys.stdin = io.StringIO("")
        observed_stop = False
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                try:
                    status = dispatch.main(argv)
                except SystemExit as exc:
                    status = exc.code
                except ValidationComplete:
                    status = None
                    observed_stop = True
        finally:
            sys.stdin = original_stdin
        row = {
            "id": f"C{index:05}",
            **case,
            "argv_resolved": argv,
            "exit_code": status,
            "stdout": stdout.getvalue(),
            "stderr": stderr.getvalue(),
            "execution": (
                "validation-passed-at-execution-boundary" if options.execution_boundary else "cookie-validation-only"
            )
            if observed_stop
            else "cli-main",
            **active,
        }
        raw_rows.append(row)
        if len(raw_rows) % 100 == 0:
            print(f"case {index}/{len(all_cases)}, {time.monotonic() - started:.1f}s", flush=True)

    # Independent OS processes validate that instrumentation has not changed the
    # visible behavior for representative parser/runtime/order/JSON cases.
    module_cases = [
        [],
        ["--list"],
        ["--unknown-option"],
        ["help"],
        ["help", "plugin"],
        ["--help"],
        ["plugin"],
        ["config", "unknown"],
        ["--host", "example.test", "plugin", "list"],
        ["plugin", "list", "--host", "example.test"],
        ["--image-format", "PNG", "download", "probe-invalid-url", "--force-image-format", "JPEG"],
        ["download", "probe-invalid-url", "--image-format", "PNG", "--force-image-format", "JPEG"],
        ["plugin", "list"],
        ["config", "path"],
        ["doctor"],
        ["--config", "relative.yaml", "doctor"],
        ["--json", "--unknown-option"],
        ["--json", "help"],
        ["--json", "download"],
        ["--json", "--list"],
        ["--selection-priority", "0", "config", "path"],
        ["--inspect-only", "plugin", "list"],
        ["--json", "config", "unknown"],
        ["--j", "--unknown-option"],
        ["--json", "--j", "--unknown-option"],
    ]
    module_rows = []
    module_cases.extend(
        [
            ["config", "profile", "--json", "init", "invalid/name"],
            ["state", "workflow", "--json", "list"],
            ["download", "https://example.test/gallery", "--fallback-generic", "disabled"],
        ]
    )
    for words in module_cases:
        if not any(row["argv_resolved"] == words for row in raw_rows):
            continue
        matching = next((r for r in raw_rows if r["argv_resolved"] == words), None)
        if options.execution_boundary and matching is not None and matching["exit_code"] not in {0, 2}:
            # A native subprocess cannot use our boundary, so only rejected
            # arguments and help are compared; successful commands are stopped.
            continue
        proc = subprocess.run(
            [sys.executable, "-m", "image_downloader", *words],
            cwd=REPO,
            env=os.environ.copy(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            encoding="utf-8",
            timeout=30,
        )
        verified = matching is not None and all(
            [
                matching["stdout"] == proc.stdout,
                matching["stderr"] == proc.stderr,
                matching["exit_code"] == proc.returncode,
            ]
        )
        module_rows.append(
            {
                "argv": words,
                "exit_code": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "matches_cli_main": verified,
            }
        )

    metadata = {
        "date": "2026-10-03" if options.execution_boundary else "2026-10-02",
        "execution_boundary": options.execution_boundary,
        "valid_url_probes": options.valid_url_probes,
        "parser_reused": options.reuse_parser,
        "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        "python": sys.version,
        "executable": sys.executable,
        "case_count": len(raw_rows),
        "root_option_count": len(specs),
        "unordered_option_pairs": len(pair_specs) * (len(pair_specs) - 1) // 2,
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "inventory": inventory,
        "module_cases": len(module_rows),
        "module_matches": sum(r["matches_cli_main"] for r in module_rows),
        "exit_counts": dict(collections.Counter(str(r["exit_code"]) for r in raw_rows)),
        "execution_counts": dict(collections.Counter(r["execution"] for r in raw_rows)),
        "workspace_fixture": str(root),
    }
    (output / "raw-results.json").write_text(
        json.dumps({"metadata": metadata, "cases": raw_rows}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output / "module-results.json").write_text(json.dumps(module_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metadata, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
