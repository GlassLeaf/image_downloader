"""Render the captured CLI investigation as Japanese comparison tables."""

from __future__ import annotations

import argparse
import collections
import csv
import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUTPUT = REPO / "docs/investigations/cli-arguments-2026-10-02"


def command_text(words):
    return "py -m image_downloader" + (" " + subprocess.list2cmdline(words) if words else "")


def explicit_options(row):
    return {w.split("=", 1)[0] for w in row["argv"] if w.startswith("--")}


def intended_invalid_options(row):  # noqa: C901 - Explicit per-command policy table used only for this investigation.
    parsed = row.get("parsed", {})
    handler = parsed.get("command_handler")
    if not handler:
        return []
    common = {
        "--config",
        "--profile",
        "--data-root",
        "--plugin-root",
        "--yes",
        "--json",
        "--plugin-verification-override",
    }
    override = {"--plugin-config", "--plugin-config-file", "--fallback-generic"}
    selection = {"--plugin", "--force-plugin"}
    policy = {"--plugin-download-policy", "--plugin-download-policy-file"}
    download = {
        "--no-console-log",
        "--existing-file",
        "--image-format",
        "--force-image-format",
        "--output-dir",
        "--directory-format",
    }
    inspection = {"--manifest-only", "--inspection-data"}
    if handler == "download":
        if parsed.get("inspect_only"):
            allowed = (
                common | override | selection | inspection | {"--inspect-only", "--output-dir", "--directory-format"}
            )
        else:
            allowed = common | override | selection | policy | download | {"--list-updated-urls", "--inspect-only"}
    elif handler == "workflow":
        allowed = (
            common
            | override
            | selection
            | policy
            | download
            | {
                "--download-scope",
                "--workflow-retries",
                "--workflow-retry-delay",
                "--workflow-retry-timeout",
                "--dry-run",
            }
        )
    elif handler == "inspect":
        allowed = common | override | selection | inspection
    elif handler == "doctor":
        allowed = common | override | {"--host", "--no-console-log", "--existing-file", "--image-format"}
    elif handler == "plugin":
        allowed = common.copy()
        if parsed.get("command_args", [None])[:1] in [["install"], ["trust"]]:
            allowed.add("--selection-priority")
    elif handler == "cookie":
        allowed = common.copy()
    elif handler == "config":
        action = parsed.get("command_args", [])
        if action == ["path"]:
            allowed = {"--json", "--plugin-verification-override"}
        elif action == ["explain"]:
            allowed = common - {"--yes"} | {"--host", "--no-console-log", "--existing-file", "--image-format"}
        elif action[:1] == ["init"]:
            allowed = {"--data-root", "--plugin-root", "--yes", "--json", "--plugin-verification-override"}
        elif action[:2] == ["profile", "init"]:
            allowed = {"--config", "--yes", "--json", "--plugin-verification-override"}
        else:
            return []
    elif handler == "state":
        allowed = {"--config", "--profile", "--data-root", "--json", "--plugin"}
        action = parsed.get("command_args", [])[1:2]
        if action == ["history"]:
            allowed.add("--limit")
        if action == ["prune"]:
            allowed.add("--dry-run")
        if action in [["run"], ["prune"]]:
            allowed.discard("--plugin")
    else:
        return []
    return sorted(explicit_options(row) - allowed - {"--help"})


def expected(row):
    if row.get("expected_hint"):
        return row["expected_hint"], "未定義コマンド・必須引数の要件"
    raw = row.get("internal_message", "")
    parsed = row.get("parsed", {})
    if row.get("parser_error"):
        return "引数エラー: " + row["parser_error"], "argparseが検出した具体的な原因（推奨する説明文）"
    invalid = intended_invalid_options(row)
    if invalid:
        return (
            f"引数エラー: {', '.join(invalid)} はこのコマンドでは指定できません",
            "CLI referenceの適用範囲＋コマンド固有オプションの意味（改善案）",
        )
    opts = explicit_options(row)
    for left, right in [
        ("--image-format", "--force-image-format"),
        ("--plugin", "--force-plugin"),
        ("--inspect-only", "--list-updated-urls"),
    ]:
        if left in opts and right in opts:
            return (
                f"引数エラー: {left} と {right} は同時に指定できません",
                "CLI referenceの排他的指定（配置によらず同じ制限）",
            )
    if (
        "--fallback-generic" in opts
        and ("--plugin" in opts or "--force-plugin" in opts)
        and parsed.get("fallback_generic") in {"enabled", "disabled"}
    ):
        return (
            "引数エラー: --plugin / --force-plugin と明示的な "
            "--fallback-generic=enabled|disabled は同時に指定できません",
            "CLI referenceの排他的指定",
        )
    if raw:
        if raw.startswith("no plugin can handle URL") and parsed.get("url"):
            return "引数エラー: URLには絶対HTTP(S) URLを指定してください（渡された値: " + str(
                parsed["url"]
            ) + "）", "試験用の非URL値／help等をdownloadへ補完しているため（改善案）"
        if row.get("exception_type") in {"ConfigurationError", "ValueError"}:
            # Do not claim the internal exception is an independent test oracle.
            return "入力・設定エラー: " + raw.replace("--plugin-id", "--plugin").replace(
                "--force-plugin-id", "--force-plugin"
            ), "実装内の具体的な検証理由から復元した改善案（独立した仕様判定ではない）"
        return "実行時エラー: " + raw, "内部例外から復元した原因説明（テスト環境に依存）"
    if row["execution"] == "cookie-validation-only":
        return "引数・設定検証を通過。Cookie操作の実行結果は対象外", "Cookie操作の直前で調査を停止"
    if row["exit_code"] == 0 and any(w in {"--help", "-h", "--he"} for w in row["argv"]):
        return "ヘルプを表示して正常終了（0）", "ヘルプオプションの通常動作"
    if row["exit_code"] == 0:
        return "正常終了（0）。結果を表示／作業用領域へ設定を作成", "既存コマンドの通常動作"
    if row["exit_code"] == 1 and parsed.get("command_handler") == "state":
        return "検索対象のstate/runが存在しないことを明示（1）", "stateのnot-found契約"
    return "解析・実行結果をstdout/stderr全文で確認", "個別確認対象"


def actual(row):
    out, err = row["stdout"].strip(), row["stderr"].strip()
    if row["execution"] == "cookie-validation-only":
        return "引数・設定検証通過（Cookie操作直前で停止。終了コード未測定）"
    try:
        payload = json.loads(out)
    except (ValueError, TypeError):
        payload = None
    if isinstance(payload, dict) and "error" in payload:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    errors = [line for line in err.splitlines() if "error:" in line or line.startswith("error [")]
    if errors:
        return "\n".join(errors)
    if row["exit_code"] == 0 and "usage:" in out:
        return "ヘルプをstdoutに表示（全文はstdout列／raw-results.json）"
    if payload and "stop_error" in payload and payload["stop_error"]:
        return "workflow結果JSON: " + json.dumps(payload["stop_error"], ensure_ascii=False)
    if "stop_error" in out or "failed" in out:
        return out[:650]
    if row["exit_code"] == 1:
        return "\n".join(err.splitlines()[-1:]) or out
    if out:
        return out if len(out) < 650 else out[:650] + " …（全文はstdout列／raw-results.json）"
    return "出力なし" if not err else "診断ログのみ（stderr全文を参照）"


def escaped(text):
    return str(text).replace("|", "\\|").replace("\r", "").replace("\n", "<br>")


def merge_parts():
    parts = [REPO / ".runtime/cli-argument-audit" / name for name in ["part-0", "part-1", "additional"]]
    results = [json.loads((p / "raw-results.json").read_text(encoding="utf-8")) for p in parts]
    rows = sorted([row for result in results for row in result["cases"]], key=lambda row: row["id"])
    assert len({row["id"] for row in rows}) == len(rows)
    assert len({tuple(row["argv"]) for row in rows}) == len(rows)
    modules = [
        item for part in parts for item in json.loads((part / "module-results.json").read_text(encoding="utf-8"))
    ]
    metadata = results[0]["metadata"].copy()
    metadata.update(
        case_count=len(rows),
        module_cases=len(modules),
        module_matches=sum(r["matches_cli_main"] for r in modules),
        elapsed_seconds=max(result["metadata"]["elapsed_seconds"] for result in results),
        workspace_fixtures=[result["metadata"]["workspace_fixture"] for result in results],
        exit_counts=dict(collections.Counter(str(row["exit_code"]) for row in rows)),
        execution_counts=dict(collections.Counter(row["execution"] for row in rows)),
    )
    metadata.pop("workspace_fixture", None)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "raw-results.json").write_text(
        json.dumps({"metadata": metadata, "cases": rows}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUTPUT / "module-results.json").write_text(json.dumps(modules, ensure_ascii=False, indent=2), encoding="utf-8")


def write_summary(rows, metadata):  # noqa: C901 - A curated, static investigation report.
    def find(words):
        found = next((r for r in rows if r["argv"] == words), None)
        assert found is not None, words
        return found

    sections = [
        (
            "提示された例と基本動作",
            [
                [],
                ["--list"],
                ["--unknown-option"],
                ["help"],
                ["help", "plugin"],
                ["--help"],
                ["--json", "--unknown-option"],
                ["--json", "--list"],
                ["--json", "help"],
            ],
        ),
        (
            "必須引数・不正値・サブコマンド",
            [
                ["download"],
                ["--json", "download"],
                ["--config"],
                ["download", "probe-invalid-url", "--image-format", "GIF"],
                ["workflow", "probe-invalid-url", "--workflow-retries", "-1"],
                ["plugin"],
                ["plugin", "unknown"],
                ["plugin", "install"],
                ["config", "unknown"],
                ["cookie", "unknown"],
                ["state", "workflow", "unknown"],
            ],
        ),
        (
            "オプションの位置による違い",
            [
                ["--host", "example.test", "plugin", "list"],
                ["plugin", "list", "--host", "example.test"],
                ["--inspect-only", "plugin", "list"],
                ["plugin", "list", "--inspect-only"],
                ["--selection-priority", "0", "config", "path"],
                ["--selection-priority", "1", "config", "path"],
                ["config", "profile", "--json", "init", "invalid/name"],
                ["config", "--json", "profile", "init", "invalid/name"],
                ["plugin", "install", "--yes", "@MISSING@"],
            ],
        ),
        (
            "排他的オプションを同じ階層／別の階層へ置く",
            [
                ["download", "probe-invalid-url", "--image-format", "PNG", "--force-image-format", "JPEG"],
                ["--image-format", "PNG", "download", "probe-invalid-url", "--force-image-format", "JPEG"],
                ["--force-image-format", "JPEG", "download", "probe-invalid-url", "--image-format", "PNG"],
                ["--image-format", "PNG", "workflow", "probe-invalid-url", "--force-image-format", "JPEG"],
                [
                    "--plugin",
                    "com.example.missing",
                    "download",
                    "probe-invalid-url",
                    "--force-plugin",
                    "com.example.missing",
                ],
                ["--inspect-only", "download", "probe-invalid-url", "--list-updated-urls"],
                ["--export-cookies", "@MISSING@", "--import-cookies", "@MISSING@"],
            ],
        ),
        (
            "configuration_errorの具体的な原因",
            [
                ["--config", "relative.yaml", "doctor"],
                ["--config", "@MISSING@", "download", "probe-invalid-url"],
                ["--config", "@bad-yaml.yaml@", "download", "probe-invalid-url"],
                ["--config", "@bad-field.yaml@", "download", "probe-invalid-url"],
                ["--plugin-config", "missing-equals", "download", "probe-invalid-url"],
                ["--plugin-config", "id={", "download", "probe-invalid-url"],
                ["--output-dir", "relative", "download", "probe-invalid-url"],
            ],
        ),
        (
            "省略記法・ヘルプ優先度・正しいURLでの後続エラー",
            [
                ["--lis"],
                ["--plugin-c"],
                ["--conf", "dummy", "doctor"],
                ["--j", "--unknown-option"],
                ["--json", "--j", "--unknown-option"],
                ["--unknown-option", "--help"],
                ["--config", "--help"],
                ["--json", "--profile", "plugin"],
                ["download", "https://example.test/gallery", "--fallback-generic", "disabled"],
            ],
        ),
    ]
    reasons = {r.get("internal_message") for r in rows if r.get("internal_message")}
    lines = [
        "# CLI引数とエラー文面の調査（2026-10-02）",
        "",
        f"**{len(rows):,}ケースを実行し、引数の組み合わせ・順序、想定される説明、実際の文面を一覧化した。** "
        "主要因は、未定義の単語をdownloadへ補完する処理、例外の具体的な理由を定型文へ置換する処理、"
        "トップレベルとサブパーサーで分かれたオプション検証である。アプリケーションの実装変更は行っていない。",
        "",
        "- [全件比較CSV](argument-comparison.csv)：引数、改善後に想定する文面、実際の文面、"
        "終了コード、内部理由、stdout/stderr全文。UTF-8 BOM付き。",
        "- [全件比較Markdown](all-cases.md)：全行の比較表。",
        "- [生データJSON](raw-results.json)：引数配列、解析結果、内部理由、stdout/stderrの全文。",
        "- [別プロセスのモジュール実行結果](module-results.json)／[環境・件数・パーサー一覧](metadata.json)。",
        "",
        "## 調査範囲と実行条件",
        "",
        f"ソースはコミット `{metadata['git_head']}`。Windows / Python 3.11.4。"
        "リポジトリの `.runtime/quality-venv/Scripts/python.exe` を使用し、"
        "`PYTHONPATH=src` で現在のソースを読み込んだ。"
        "通常の `py` では当初このリポジトリのパッケージが見つからなかったため、一覧の `py -m image_downloader` は"
        "ユーザーの入力に合わせた同等コマンドの表記であり、実測のPython実行ファイルはmetadataに記録している。",
        "",
        "| 対象 | 範囲 |",
        "| --- | --- |",
        "| コマンド | download / workflow / inspect / doctor / config / plugin / cookie / state "
        "の8種、21の代表的な操作形式 |",
        "| オプション | ヘルプ以外のトップレベル35オプション全て。--help / -h、省略指定も追加 |",
        "| 順序 | コマンドの前、コマンドと引数の間、引数の後。代表的な多段サブコマンドの途中も追加 |",
        "| 組み合わせ | --json以外の34オプションの全561組を両順序で指定。JSON有無を追加。"
        "代表的な排他指定はパーサー階層をまたぐ配置も検証 |",
        "| 不正入力 | 未知のオプション・コマンド、必須値不足、余剰引数、選択値違反、数値形式・範囲、"
        "相対パス、存在しないファイル、不正YAML/JSON、空値、=形式、重複指定、--区切り |",
        f"| 結果 | {metadata['execution_counts'].get('cli-main', 0):,}件はCLI mainを実行。"
        f"{metadata['execution_counts'].get('cookie-validation-only', 0):,}件はCookie操作直前までの検証に限定 |",
        f"| 照合 | 代表{metadata['module_cases']}件を別プロセスの `python -m image_downloader` で実行し、"
        f"stdout・stderr・終了コードが{metadata['module_matches']}/{metadata['module_cases']}件一致 |",
        f"| 内部理由 | 固定文面へ置換される前の具体的な理由を{len(reasons)}種類記録（パス・値が違う理由を含む） |",
        "",
        "実際のユーザー設定・保存データ・プラグインには触れず、platformdirsの `WIN_PD_OVERRIDE_LOCAL_APPDATA` / "
        "`WIN_PD_OVERRIDE_APPDATA` を作業用ディレクトリへ向けた。正常な最小設定と空のプラグイン領域を用意し、"
        "外部サイトの取得が発生しない `probe-invalid-url` を通常のURL引数に使った。正しいHTTP(S) URLを使う追加試験では"
        "generic fallbackを無効化した。したがって対象プラグインが無い実行時エラーは、"
        "引数の解析失敗と区別する必要がある。",
        "",
        "Cookieはオプション検証と設定解決を通過した時点で調査用の停止を入れた。Cookie読み書き・ブラウザCookieの取得・"
        "パスフレーズ入力は実行していない。これらの行の終了コードは未測定で、成功したと断定しない。"
        "設定の作成・プラグイン管理の試験は作業用領域に限定した。",
        "",
        "引数値・オプション数・重複回数・未知の単語は無限に存在するため、完全な全順列の列挙ではない。"
        "全オプションとペアを基礎に、意味の異なる検証経路・配置を広く調べた。外部通信、実プラグインのインストール成功、"
        "Cookie操作の成功、ダウンロード成功などの後続処理の文面は今回の対象外である。",
        "",
        "## 比較表の読み方",
        "",
        "「本来想定される文面」は、原因の特定を助ける**改善案**であり、決定済みの新仕様ではない。"
        "コマンド適用範囲・排他指定は [CLI reference](../../v3/reference/cli.md) を基礎にし、"
        "既存の具体的な検証理由から復元した説明には、CSVの「期待文面の根拠」列でその旨を示した。"
        "内部例外を使った説明の復元は独立した仕様テストではない。複数の誤りがある場合の優先順は今後の修正時に決める必要がある。",
        "",
        "以下は代表例。共通の `py -m image_downloader` は省略。`@MISSING@` は作業用の存在しない絶対パス、"
        "`@bad-*.yaml@` は作業用の不正設定ファイル。実際に渡したパスはCSV/JSONに記録している。"
        "stderrにはエラー以外にusageや4行の設定診断が付く場合があり、表ではエラー部分を中心に示す。",
        "",
    ]
    for title, words_list in sections:
        lines += [
            "### " + title,
            "",
            "| ID | 引数 | 本来想定される文面・動作（改善案） | 実際に返る文面・動作 | 終了コード |",
            "| --- | --- | --- | --- | --- |",
        ]
        for words in words_list:
            row = find(words)
            vals = [
                row["id"],
                "`" + (subprocess.list2cmdline(words) or "(引数なし)") + "`",
                row["expected_message"],
                row["actual_summary"],
                row["exit_code"],
            ]
            lines.append("| " + " | ".join(escaped(v) for v in vals) + " |")
        lines.append("")
    lines += [
        "## 原因と修正時に考慮すべき点",
        "",
        "1. **`--list` は未知のオプションとして拒否されていない。** argparseの省略記法によって `--list-updated-urls` "
        "へ解釈される。URL/commandが無いため `ValueError('URL or command is required')` が発生し、"
        "`configuration_error` の定型文へ置換される。省略記法を維持するか、"
        "`allow_abbrev=False` にするかを判断する必要がある。",
        "",
        "2. **`help` はpluginコマンドへ振り分けられているわけではない。** "
        "[_normalize_cli_arguments](../../../src/image_downloader/commands/parser.py) "
        "は最初の未定義の非オプション単語の前へ "
        "`download` を挿入する。`help` はURLとして渡され、対応プラグインが見つからず `PluginError` になる。"
        "`help plugin` は補完後の `download help plugin` の余剰引数 `plugin` として解析エラーになる。"
        "未知コマンドを拒否するか、`help` を正式な別名として扱い、裸URL補完をHTTP(S) URLへ限定すると原因が明確になる。",
        "",
        "3. **内部には具体的な理由があるが、表示時に消えている。** "
        "[error_info_for](../../../src/image_downloader/exceptions.py) は `str(error)` を公開せず、"
        "`message` を固定の `reason` と同じにする。"
        "さらに [_cli_error_info](../../../src/image_downloader/commands/dispatch.py) "
        "は通常のValueErrorをメッセージ無しのConfigurationErrorへ変換する。引数・設定検証の安全な情報を構造化して"
        "渡す仕組みが必要で、任意の例外文字列を無条件に表示する変更は既存の機密情報保護の契約と衝突する。",
        "",
        "4. **JSONの構文エラー経路だけが定型文へ入る。** "
        "[_CliArgumentParser.error](../../../src/image_downloader/commands/parser.py) は `--json` の完全一致があると "
        "ConfigurationErrorを投げる。そのため未知オプション・不足値・不正な選択値まで説明を失う。"
        "`--j` という省略表記は解析上JSONとして受理されても、構文エラー時のJSON判定には使われず、"
        "通常の解析エラーになる。"
        "また `_operation_name` は解析前に値として出現した `plugin` などもcommandとして推測しうる。",
        "",
        "5. **パーサー階層をまたぐ排他指定は一貫しない。** "
        "トップレベルと各サブパーサーが別々にmutually_exclusive_groupを持つため、コマンド前後へ分けると "
        "両値が残る。workflowは後段でも競合を検証するが、downloadやinspectには同等の検証が無い組み合わせがある。"
        "配置に依存しない共通の検証が必要である。",
        "",
        "6. **非対応オプションの拒否には位置・値による穴がある。** "
        "[_reject_command_options](../../../src/image_downloader/commands/validation.py) は明示指定を見ず、"
        "既定値との差だけを見る。例えば `--selection-priority 0 config path` は通過し、1なら拒否される。"
        "`--inspect-only plugin list` のように、一部ハンドラーではそもそも拒否一覧に無いフラグが無視される。"
        "stateは [_explicit_options](../../../src/image_downloader/commands/state.py) を使った別の検証を持つ。"
        "多段の自由な位置引数 `nargs='*'` の途中へオプションを入れると残りが余剰引数になるケースもある。",
        "",
        "## 再実行",
        "",
        "```powershell",
        ".runtime/quality-venv/Scripts/python.exe tools/audit_cli_arguments.py",
        ".runtime/quality-venv/Scripts/python.exe tools/render_cli_argument_audit.py",
        "```",
        "",
        "調査スクリプトは実行ごとに新しい作業用領域を作る。初回調査の保存不備を修正した後、"
        "全件を2つの独立したプロセスへ分割して再実行し、追加の配置・URL試験を統合した。"
        "`metadata.json` の実行時間は最も長い分割実行の秒数であり、調査全体の所要時間ではない。",
        "",
    ]
    (OUTPUT / "report.md").write_text("\n".join(lines), encoding="utf-8")


def render():
    data = json.loads((OUTPUT / "raw-results.json").read_text(encoding="utf-8"))
    rows, metadata = data["cases"], data["metadata"]
    module_rows = json.loads((OUTPUT / "module-results.json").read_text(encoding="utf-8"))
    for module in module_rows:
        matching = next((r for r in rows if r["argv_resolved"] == module["argv"]), None)
        module["matches_except_program_name"] = matching is not None and all(
            [
                matching["stdout"].replace("audit_cli_arguments.py", "__main__.py") == module["stdout"],
                matching["stderr"].replace("audit_cli_arguments.py", "__main__.py") == module["stderr"],
                matching["exit_code"] == module["exit_code"],
            ]
        )
    metadata["module_matches_except_program_name"] = sum(r["matches_except_program_name"] for r in module_rows)
    (OUTPUT / "module-results.json").write_text(json.dumps(module_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUTPUT / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = [
        "ID",
        "試験分類",
        "コマンド引数（記号置換前）",
        "同等のコマンド（引数展開後、py表記）",
        "本来想定される文面・動作（改善案）",
        "期待文面の根拠",
        "実際に返る文面（要約）",
        "終了コード",
        "内部例外型",
        "内部の具体的な理由",
        "実行範囲",
        "stdout全文",
        "stderr全文",
        "解析された引数JSON",
    ]
    md = [
        "# CLI引数・順序とエラー文面の全件一覧",
        "",
        "各行の比較対象は改善案です。既存仕様の合否表ではありません。全文・根拠・解析結果はCSV/JSONを参照してください。",
        "",
        "| ID | 引数 | 本来想定される文面・動作（改善案） | 実際の文面・動作 | 終了コード |",
        "| --- | --- | --- | --- | --- |",
    ]
    with (OUTPUT / "argument-comparison.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            expectation, basis = expected(row)
            row["expected_message"], row["expected_basis"] = expectation, basis
            row["actual_summary"] = actual(row)
            writer.writerow(
                dict(
                    zip(
                        fields,
                        [
                            row["id"],
                            row["family"],
                            subprocess.list2cmdline(row["argv"]),
                            command_text(row["argv_resolved"]),
                            expectation,
                            basis,
                            row["actual_summary"],
                            row["exit_code"],
                            row.get("exception_type", ""),
                            row.get("internal_message", ""),
                            row["execution"],
                            row["stdout"],
                            row["stderr"],
                            json.dumps(row.get("parsed", {}), ensure_ascii=False),
                        ],
                        strict=True,
                    )
                )
            )
            md.append(
                "| "
                + " | ".join(
                    escaped(v)
                    for v in [
                        row["id"],
                        "`" + subprocess.list2cmdline(row["argv"]) + "`",
                        expectation,
                        row["actual_summary"],
                        row["exit_code"],
                    ]
                )
                + " |"
            )
    (OUTPUT / "all-cases.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    # The enriched JSON remains machine-readable and contains the verbatim streams.
    (OUTPUT / "raw-results.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    write_summary(rows, metadata)
    print(
        json.dumps(
            {
                "cases": len(rows),
                "csv": str(OUTPUT / "argument-comparison.csv"),
                "internal_reasons": len({r.get("internal_message") for r in rows if r.get("internal_message")}),
                "silent_incompatible": sum(
                    bool(intended_invalid_options(r)) and not r.get("parser_error") and not r.get("internal_message")
                    for r in rows
                ),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--merge-parts", action="store_true")
    options = parser.parse_args()
    if options.merge_parts:
        merge_parts()
    render()
