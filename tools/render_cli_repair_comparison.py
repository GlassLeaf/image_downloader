"""Render preserved CLI audit cases against a separate, bounded replay."""

from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import shlex
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def error(row: dict) -> dict:
    try:
        payload = json.loads(row["stdout"])
    except (ValueError, KeyError):
        return {}
    return payload.get("error", {}) if isinstance(payload, dict) else {}


def message(row: dict) -> str:
    payload = error(row)
    if payload:
        return payload["message"]
    lines = row.get("stderr", "").splitlines()
    errors = [line for line in lines if line.startswith("error") or "unrecognized arguments:" in line]
    if errors:
        return "\n".join(errors)
    if row["execution"] == "validation-passed-at-execution-boundary":
        return "validation passed; execution stopped (not execution success)"
    if row.get("stdout", "").startswith("usage:"):
        return "help displayed"
    return row.get("stderr", "") or row.get("stdout", "")


def code(row: dict) -> str:
    payload = error(row)
    if payload:
        return payload["code"]
    text = row.get("stderr", "")
    marker = "error ["
    if marker in text:
        return text.split(marker, 1)[1].split("]", 1)[0]
    if row["execution"] == "validation-passed-at-execution-boundary":
        return "validation_passed"
    return "help" if row.get("stdout", "").startswith("usage:") else "legacy_output"


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--baseline", type=Path, default=REPO / "docs/investigations/cli-arguments-2026-10-02/raw-results.json"
    )
    parser.add_argument("--output", type=Path, default=REPO / "docs/investigations/cli-arguments-2026-10-03")
    args = parser.parse_args()
    output = args.output.resolve()
    assert output != args.baseline.resolve().parent
    original = read(args.baseline)
    replay = read(output / "invalid-input-replay/raw-results.json")
    valid_urls = read(output / "valid-url-probes/raw-results.json")
    before = {row["id"]: row for row in original["cases"]}
    after = {row["id"]: row for row in replay["cases"]}
    assert set(before) == set(after) and len(before) == 8794
    rows = []
    for identity, old in before.items():
        new = after[identity]
        assert old["argv"] == new["argv"]
        rows.append(
            {
                "id": identity,
                "family": old["family"],
                "arguments_in_order": shlex.join(old["argv"]),
                "original_expected_hint": old.get("expected_hint", ""),
                "before_exit": old["exit_code"],
                "before_code": code(old),
                "before_message": message(old),
                "after_exit": new["exit_code"],
                "after_code": code(new),
                "after_message": message(new),
                "after_details": json.dumps(error(new).get("details", {}), ensure_ascii=False),
                "after_operation": error(new).get("operation", ""),
                "after_execution": new["execution"],
            }
        )
    write_csv(output / "before-after-comparison.csv", rows)
    url_rows = []
    for new in valid_urls["cases"]:
        old = after[new["id"]]
        assert "probe-invalid-url" in old["argv"]
        url_rows.append(
            {
                "id": new["id"],
                "invalid_url_arguments": shlex.join(old["argv"]),
                "invalid_url_code": code(old),
                "invalid_url_message": message(old),
                "valid_url_arguments": shlex.join(new["argv"]),
                "valid_url_exit": new["exit_code"],
                "valid_url_code": code(new),
                "valid_url_message": message(new),
                "execution": new["execution"],
            }
        )
    write_csv(output / "valid-url-comparison.csv", url_rows)
    summary = {
        "baseline_sha256": hashlib.sha256(args.baseline.read_bytes()).hexdigest(),
        "case_count": len(rows),
        "url_twin_count": len(url_rows),
        "after_codes": dict(collections.Counter(row["after_code"] for row in rows)),
        "url_twin_codes": dict(collections.Counter(row["valid_url_code"] for row in url_rows)),
        "module_cases": replay["metadata"]["module_cases"],
        "module_matches": replay["metadata"]["module_matches"],
        "execution_boundary": True,
    }
    (output / "comparison-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    examples = [
        ["--list"],
        ["--unknown-option"],
        ["help"],
        ["help", "plugin"],
        [],
        ["--host", "example.test", "plugin", "list"],
        ["plugin", "list", "--host", "example.test"],
    ]
    lines = [
        "# CLI 引数修正後の比較 (2026-10-03)",
        "",
        "元の8,794件を同じID・引数順序で再評価した。元資料は変更していない。",
        "非URL値を使った元ケースと、有効な絶対HTTP(S) URLへ置換した3,593件を分離した。",
        "有効入力は設定を読み取り専用で解決した後、ハンドラー実行の直前で停止した。",
        "config path/init/profile init はその専用処理の前で停止しているため、設定作成の成功も判定していない。",
        "`validation_passed` は「検証通過」を表し、ダウンロード・プラグイン操作・Cookie操作の実行成功を意味しない。",
        "",
        "- [全8,794件の修正前後比較](before-after-comparison.csv)",
        "- [有効URL置換3,593件の比較](valid-url-comparison.csv)",
        "- [元の調査結果](../cli-arguments-2026-10-02/report.md)",
        "- [分類集計と元資料のSHA-256](comparison-summary.json)",
        "- [実装・検証記録](verification.md)",
        "",
        "| 引数の組み合わせ・順序 | 修正前 | 修正後 |",
        "| --- | --- | --- |",
    ]
    for argv in examples:
        identity = next(key for key, row in before.items() if row["argv"] == argv)
        old_text = message(before[identity]).replace("\n", "<br>").replace("|", "\\|")
        new_text = message(after[identity]).replace("\n", "<br>").replace("|", "\\|")
        lines.append(f"| `{shlex.join(argv) or '(引数なし)'}` | {old_text} | {new_text} |")
    lines.extend(["", "## 修正後の分類", "", "| 分類 | 元ケース | 有効URL置換 |", "| --- | ---: | ---: |"])
    for category in sorted(set(summary["after_codes"]) | set(summary["url_twin_codes"])):
        lines.append(
            f"| {category} | {summary['after_codes'].get(category, 0)} | {summary['url_twin_codes'].get(category, 0)} |"
        )
    lines.extend(
        [
            "",
            f"独立したモジュール実行による照合: {summary['module_matches']}/{summary['module_cases']}件一致。",
            "モジュール照合は引数不正・ヘルプのみで、境界停止が必要な有効入力は通常のモジュール実行へ渡していない。",
            "元の expected_hint は調査時の改善案として保存しており、実行成功の独立した判定基準ではない。",
            "",
        ]
    )
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
