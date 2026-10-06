"""Render the curated findings and validate them against captured evidence."""

from __future__ import annotations

import ast
import csv
import hashlib
import io
import json
import re
import tokenize
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RAW = HERE / "raw"
SHORT_IMPACT = {
    "QS-001": "実行ユーザー権限で未署名コード実行。管理者への昇格は未確認。",
    "QS-002": "パスワード・トークン・fragmentを外部画像サーバーへ送信。",
    "QS-003": "メモリ・CPU枯渇、処理・終了待機の長期化。実害を起こす負荷試験は未実施。",
    "QS-004": "別の取得対象の画像を上書きして失う。",
    "QS-005": "画像未取得を完了と誤認し、次回updatedで取得しない。",
    "QS-006": "設定が既定へ戻り、元の設定値もファイルから消える。",
    "QS-007": "非画像・破損内容の見落とし。passedは存在検査の成功。",
    "QS-008": "欠損が復旧せず、別の保存条件での取得も行われない。",
    "QS-009": "後続フレーム・動き・時間情報を失う。",
    "QS-010": "巨大整数でOverflowErrorとなり設定エラー診断が欠ける。",
    "QS-011": "不正cookieデータでAttributeError/KeyError、復旧操作も失敗し得る。",
    "QS-012": "一部宛先に通知が届かず、拒否結果を見落とす。",
    "QS-013": "組立て失敗後もmoduleが残り、同一プロセスの再試行で蓄積。",
    "QS-016": "別プラグインが同じ環境変数の秘密を受け取る。外部送信は未実測。",
}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def location(file: str, needle: str) -> dict[str, object]:
    path = ROOT / "src/image_downloader" / file
    matches = [i for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1) if needle in line]
    if len(matches) != 1:
        raise ValueError(f"location must be unique: {file}, {needle!r}: {matches}")
    return {"file": path.relative_to(ROOT).as_posix(), "line": matches[0]}


def reference(item: dict) -> str:
    file, line = item["file"], item["line"]
    label = f"{file.removeprefix('src/image_downloader/')}:{line}"
    return f"[{label}](../../../{file}#L{line})"


def write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def style_occurrences() -> list[dict]:
    result = []
    for alert in read_json(RAW / "ruff-pep8.stdout"):
        result.append(
            {
                "ID": "QS-014" if alert["code"] == "E501" else "QS-015",
                "規則": alert["code"],
                "ファイル": Path(alert["filename"]).relative_to(ROOT).as_posix(),
                "行": alert["location"]["row"],
                "列": alert["location"]["column"],
                "説明": alert["message"],
            }
        )
    for path in sorted((ROOT / "src/image_downloader").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        rows = set()
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT:
                rows.add(token.start[0])
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.body and isinstance(node.body[0], ast.Expr):
                    value = node.body[0].value
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        rows.update(range(value.lineno, value.end_lineno + 1))
        for number in sorted(rows):
            line = source.splitlines()[number - 1]
            if len(line) > 72:
                result.append(
                    {
                        "ID": "QS-014",
                        "規則": "PEP8-documentation-72-measurement",
                        "ファイル": path.relative_to(ROOT).as_posix(),
                        "行": number,
                        "列": 73,
                        "説明": f"文書行の長さ={len(line)}。URL・コード例などの適用除外を断定しない測定値。",
                    }
                )
    write_csv(HERE / "style-occurrences.csv", result, ["ID", "規則", "ファイル", "行", "列", "説明"])
    return result


def security_triage() -> list[dict]:
    explanations = {
        "S101": (
            "外部入力は別の明示検証があり、assertは内部の型・状態不変条件。"
            "最適化で除去されるが、今回の精読では単独の認証・範囲検査回避を確認しない。"
        ),
        "S105": "tokenはCLI引数または出力書式の予約文字列。認証パスワードの固定値ではない。",
        "S110": "既存の主例外・保存結果を優先し、二次的な診断・stderr失敗を抑制する意図。関連v3異常系テストも成功。",
        "S311": "random.uniformは再試行待機のjitterにのみ使用。鍵・nonce・認証tokenの生成ではない。",
    }
    rows = []
    for alert in read_json(RAW / "ruff-security.stdout"):
        rows.append(
            {
                "規則": alert["code"],
                "ファイル": Path(alert["filename"]).relative_to(ROOT).as_posix(),
                "行": alert["location"]["row"],
                "検出": alert["message"],
                "判定": "確定脆弱性として採用しない",
                "根拠": explanations[alert["code"]],
            }
        )
    write_csv(HERE / "security-triage.csv", rows, ["規則", "ファイル", "行", "検出", "判定", "根拠"])
    return rows


def finding_csv(findings: list[dict]) -> None:
    rows = []
    for item in findings:
        sites = [f"{site['file']}:{site['line']}" for site in item["resolved_locations"]]
        if item["category"].startswith("書式"):
            sites.append("全箇所: style-occurrences.csv")
        rows.append(
            {
                "ID": item["id"],
                "区分": item["category"],
                "問題の種類": item["kind"],
                "重要度": item["severity"],
                "確認状態": item["status"],
                "仕様との関係": item["contract"],
                "問題": item["title"],
                "詳細": item["problem"],
                "成立条件": item["conditions"],
                "エラー・被害": item["impact"],
                "該当箇所": "; ".join(sites),
                "根拠": item["evidence"],
                "CWE": "; ".join(item["cwe"]) or "該当なし（品質・仕様・書式）",
                "参考観点": item["principles"],
                "改善案": item["recommendation"],
                "過去ID": "; ".join(item["previous"]),
            }
        )
    write_csv(HERE / "findings.csv", rows, list(rows[0]))


def summary_table(findings: list[dict]) -> str:
    result = [
        "| ID | 区分・問題の種類 | 重要度・確認 | 問題 | 起こり得るエラー・被害 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in findings:
        ident = item["id"]
        result.append(
            f"| [{ident}](#{ident.lower()}) | {item['category']}：{item['kind']} | "
            f"{item['severity']}・{item['status']} | {item['title']} | {SHORT_IMPACT[ident]} |"
        )
    return "\n".join(result)


def detail(item: dict) -> str:
    refs = "、".join(reference(site) for site in item["resolved_locations"]) or "全箇所はstyle-occurrences.csv"
    cwes = []
    for name in item["cwe"]:
        number = re.search(r"CWE-(\d+)", name).group(1)
        cwes.append(f"[{name}](https://cwe.mitre.org/data/definitions/{number}.html)")
    classification = "、".join(cwes) or "CWEは付けない（品質・仕様・書式上の指摘）"
    return f"""<a id="{item["id"].lower()}"></a>

### {item["id"]} — {item["title"]}

**{item["severity"]}・{item["status"]}**。種類：{item["kind"]}。{item["contract"]}。

{item["problem"]}

- 成立条件：{item["conditions"]}
- エラー・被害：{item["impact"]}
- 根拠：{item["evidence"]}
- 該当箇所：{refs}
- 分類・参考観点：{classification}。{item["principles"]}。
- 改善案：{item["recommendation"]}
"""


def render(findings: list[dict], metadata: dict, styles: list[dict], alerts: list[dict]) -> None:
    coverage = read_json(RAW / "coverage.json")["totals"]
    confirmed = [f for f in findings if f["status"] != "未確定" and not f["category"].startswith("書式")]
    style_findings = [f for f in findings if f["category"].startswith("書式")]
    pending = [f for f in findings if f["status"] == "未確定"]
    measured = sum(s["規則"] == "PEP8-documentation-72-measurement" for s in styles)
    style_table = (
        "| [QS-014](#qs-014) | 行長 | Ruff E501：1819箇所。"
        f"文書行72字超の計測：{measured}箇所（E501との重複あり）。 | "
        "可読性。実行・セキュリティ上の被害は示さない。 |\n"
        "| [QS-015](#qs-015) | 例外名 | N818：2箇所。UnsupportedSiteFeature、SecretNotFound。 | "
        "命名方針との一貫性。公開名変更には互換性の検討が必要。 |"
    )
    pending_table = (
        "| [QS-017](#qs-017) | 保存先・lockの検証と使用間のリンク差替え | "
        "共有rootへの攻撃者書込みが必要。OS上の競合・実害は未再現。確定脆弱性に含めない。 |"
    )
    checks = "\n".join(
        f"| {c['name']} | {c['exit_code']} | {c['seconds']:.2f}秒 | [stdout](raw/{c['name']}.stdout) |"
        for c in metadata["checks"]
    )
    previous = "\n".join(
        f"| {', '.join(item['previous'])} | {item['id']} | 現行コードで再確認 |"
        for item in confirmed
        if item["previous"]
    )
    body = f"""# 本体コードの品質・セキュリティ監査

監査日：2026-10-06（Asia/Tokyo）。基準コミット：`{metadata["commit"]}`。
Python本体93ファイル・18,054行を自動解析し、信頼境界・異常処理・永続化・状態判定を重点精読した。
**確認した問題・安全性／品質上の不足は14項目、書式の差分は2分類、未確定事項は1項目。**
現行契約に沿った挙動も含むため、14項目すべてを実装バグや脆弱性とは呼ばない。

優先対象はQS-001（条件付きの未署名コード実行）、QS-002（Refererによる秘密情報送信）、
QS-003（画像展開負荷・待機制御）、QS-004（別対象の画像上書き）である。
取得画像だけからの遠隔コード実行、一般ユーザーから管理者への特権昇格は確認していない。

## 対象・評価方法

一般ユーザー権限のCLI利用を前提に、取得先のHTML・画像・HTTP応答を信頼できない入力とした。
対象は`src/image_downloader`、本体のプラグイン署名・catalog・読込み機構、関連v3テスト、
設定・現行文書。外部プラグインの中身、旧版、依存ライブラリ内部・既知CVEの網羅調査、
第三者がURLを投入するサーバーの脅威モデルは対象外。

[PEP 8](https://peps.python.org/pep-0008/)は書式・命名・可読性、
[CERT-Cの規則一覧](https://cmu-sei.github.io/secure-coding-standards/sei-cert-c-coding-standard/rules/)は
初期化、変換範囲、戻り値・エラー処理、資源管理の参考にした。
[CERT-Cの適用範囲](https://cmu-sei.github.io/secure-coding-standards/sei-cert-c-coding-standard/front-matter/introduction/scope/)はC言語であり、
Pythonへの正式な準拠判定ではない。MISRA-Cも安全な入力・制御・資源管理という観点で参考にし、
[MISRA Compliance:2020](https://misra.org.uk/app/uploads/2021/06/MISRA-Compliance-2020.pdf)のような
条項別の適合証明や全MISRA条項の評価は行っていない。
これらは単一のテストセットではなく、規約・弱点分類・設計上の観点である。

重要度は、実行権限・入力を操作できる主体・影響・起こりやすさを合わせて評価した。
「高」でも成立条件を省略しない。「再現済み」は観測した挙動を指し、記載したすべての被害を
実際に発生させた意味ではない。DoSなどの被害予測は根拠と限界を明示した。
品質・仕様上の課題に無理にCWEを割り当てず、セキュリティ項目は具体的な弱点を対応付けた。

## 問題一覧

IDは根本原因単位で一意に付与した。既存の過去IDとは別のnamespaceとし、下記で対応付ける。
成立条件・ソース行・再現結果・改善案は各IDの詳細および[全件CSV](findings.csv)に含む。

{summary_table(confirmed)}

## 書式・保守性の別表

| ID | 観点 | 件数・評価 | 影響 |
| --- | --- | --- | --- |
{style_table}

本体の既存Ruff規約（120字）では違反0件。PEP 8自身もプロジェクト固有規約を優先する。
全該当箇所・行・規則は[style-occurrences.csv](style-occurrences.csv)。
docstring・コメントの72字超は機械計測で、URLやコード例の個別例外を断定しない。
同じ行にE501と文書行計測の2レコードがある場合も、2つの実行不具合とは数えない。

## 未確定事項

| ID | 候補 | 成立条件・未確認点 |
| --- | --- | --- |
{pending_table}

## 実行した検査と結果

環境：{metadata["platform"]}、Python {metadata["python"].split()[0]}。
ツール：{", ".join(f"{name} {version}" for name, version in metadata["tools"].items())}。
起動時点で既存の未追跡ファイルは`docs/investigations/implementation-review-2026-10-05/`。
既存資料を保持し、製品ソース・API・設定・既存テストを変更していない。
開始時状態、UTC実行時刻、コマンド、終了コード、全93ファイルのSHA-256は
[metadata.json](raw/metadata.json)に保存した。監査後にも全SHA-256を照合した。

| 検査 | 終了コード | 時間 | 記録 |
| --- | --- | --- | --- |
{checks}

- 本体Ruff：検出0件。mypy：93ファイルで指摘なし。
- 追加セキュリティ規則：{len(alerts)}件。S101=26、S105=5、S110=5、S311=1。
  全件精読し、内部不変条件、予約トークン、二次診断失敗の抑制、非暗号用途のjitterと判断した。
  この37件から確定脆弱性は採用しない。全箇所と判定は[security-triage.csv](security-triage.csv)。
- 厳格な追加書式検査：E501=1819、N818=2。規則に対する検出で終了コード1となる。
  自動解析の異常終了とは区別する。Ruffの全PEP 8推奨を網羅する検査ではない。
- v3全体：**1453 passed、16 skipped**。
  カバレッジ（statement+branch）：**{coverage["percent_covered"]:.2f}%**。
  statement：{coverage["percent_statements_covered"]:.2f}%、branch：{coverage["percent_branches_covered"]:.2f}%。
  75%の既存gateは通過。未実行statementは943、未網羅branchは624。
- スキップはMailpit opt-inの12件と、symlinkを作成できないWindows環境での4件。
  4件を限定再実行して理由を確認した：[symlink-checks.stdout](raw/symlink-checks.stdout)。
- 監査プローブ：**18 passed**。[probes.stdout](raw/probes.stdout)、[JUnit](raw/probes.xml)、
  [コマンド・終了コード](raw/probes-metadata.json)。
  小さな画像、temporary directory、MockTransport、fake SMTP、架空の秘密のみを使用。
  実メール・実アカウントの秘密・メモリ枯渇・無期限worker停止は使っていない。
- プローブは現行の問題挙動をassertする調査用資料。成功は修正済みという意味ではない。
  v3の正常仕様テストには組み込んでいない。

Ruff・mypy・pytestは現行契約への整合を示し、今回の設計・安全性の不足がないことを証明しない。
Bandit、pycodestyle、pip-auditは当該環境に未導入で、今回は新規導入せずRuff追加規則と精読を使った。
他OS・他Python版は今回実行していない。CIのmatrix定義と今回のWindows/Python 3.11実測を区別する。

## 確認した防御・指摘しなかった事項

- 未定義・初期化前の変数使用は、今回の本体Ruff/mypyと重点精読で確定指摘を得なかった。
  Python以外のライブラリ内部の未初期化メモリまで調べたものではない。
- HTTP(S)以外の送信先の拒否、クロスオリジンredirect時の認証ヘッダー除去、
  bodyの宣言サイズと実測サイズの上限、origin範囲を持つ認証flowの検証がある。
  監査のpositive-controlで認証ヘッダー除去とサイズ超過拒否も確認した。
  この防御がQS-002の自動Refererまで覆うわけではない。
- 通常のpath traversal、危険な要素名、link/reparse/hard linkの検査、原子的保存、
  core同士のプロセス間lockがある。共有rootでの悪意ある競合の保証はQS-017に分ける。
- cookieはAES-GCM、exportはscryptを使用し、鍵はkeyring、nonce等はsecretsから生成する。
  固定暗号鍵・randomによる認証nonce生成は確認しなかった。構造検証の不足はQS-011に分ける。
- warn/off/bypassを利用者が明示して信頼検証を弱める挙動は、その設定の仕様として扱う。
  信頼済みPythonプラグインの意図した実行をコード実行脆弱性として重複計上しない。
- 任意URLを取得するCLIでprivate IPへの取得自体をSSRFと断定しない。
  第三者がURLを投入するサーバー組込みの評価では、別の脅威モデルと対策が必要となる。

## 過去レビューとの対応

| 過去ID | 今回ID | 扱い |
| --- | --- | --- |
{previous}

前報告のR01・R02・R04・R09・R15は、同報告で解決済みとされており、今回の未解決項目に再掲しない。
R08（srcset等）、R13（次回起動の画像単位再開）、R16（同URLの画像本文更新）は、
文書に示された対応範囲の拡張要望として参照し、独立した実装バグとは計上しない。
R10の直接画像URLの空成功はQS-005と共通の結果判定に含め、今回の独立プローブは空HTMLを対象とした。
R07（導入デモ）、R18（保守文書の整理）は本体優先の監査範囲外。
過去報告の実行結果を今回の再現結果として流用していない。

## 再実行

既存の品質用venvから次を実行する。別環境では同等の依存を備えたPythonへ置き換える。

```powershell
$auditPython = ".runtime/quality-venv/Scripts/python.exe"
$auditDirectory = "docs/investigations/quality-security-review-2026-10-06"
& $auditPython "$auditDirectory/audit_checks.py"
& $auditPython -m pytest "$auditDirectory/behavior_probe.py" -q -s
& $auditPython "$auditDirectory/build_report.py"
```

最初のコマンドは全v3テストを含み、今回約10分を要した。raw出力を更新する。
監査日の基準コミット・初期作業状態は今回の記録であり、将来の再実行ではその時点の記録と分ける。
build_reportは基準ソースのSHA-256と記載した行の一意性を検証し、本文・CSVを再生成する。

## 問題の詳細

"""
    body += "\n".join(detail(item) for item in [*confirmed, *style_findings, *pending])
    (HERE / "report.md").write_text(body.rstrip() + "\n", encoding="utf-8")


def main() -> None:
    metadata = read_json(RAW / "metadata.json")
    for source in metadata["inventory"]:
        digest = hashlib.sha256((ROOT / source["path"]).read_bytes()).hexdigest()
        if digest != source["sha256"]:
            raise ValueError(f"production file changed: {source['path']}")
    findings = read_json(HERE / "findings.json")
    if {f["id"] for f in findings} != {f"QS-{n:03d}" for n in range(1, 18)} or len(findings) != 17:
        raise ValueError("finding IDs must be unique and complete")
    for item in findings:
        item["resolved_locations"] = [location(*pair) for pair in item["locations"]]
    styles = style_occurrences()
    alerts = security_triage()
    if read_json(RAW / "probes-metadata.json")["exit_code"] != 0:
        raise ValueError("probe failures must be reviewed")
    finding_csv(findings)
    render(findings, metadata, styles, alerts)
    (RAW / "resolved-findings.json").write_text(
        json.dumps(findings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "findings": len(findings),
                "style_records": len(styles),
                "style_rules": dict(Counter(row["規則"] for row in styles)),
                "security_alerts_reviewed": len(alerts),
                "production_fingerprints_unchanged": len(metadata["inventory"]),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
