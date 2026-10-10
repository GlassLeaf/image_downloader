# 履歴ロックの実行ごとの再現性調査

調査日：2026-10-11（JST）。ユーザーの依頼に基づき、同条件の再実行と対照比較を行った。
対象は `codex/fix-qs-013-construction-cleanup` の `0d63138`。
[別環境の初回調査](workflow-history-alternate-environment-2026-10-11.md)の後に実行した追加記録である。

## 結果と解釈

現行コードは、新しい親 Python で開始した5実行すべてで初期化 flush の PermissionError を再現した。
1実行600回あたりの取得エラーは **7・9・7・11・12回**だった。
初期化を省いた隔離コピーは、5実行・計3,000回でエラー0件だった。
元の履歴保存テストは、新しい Python プロセスで20回実行し、
20回成功・0回失敗だった。

**個々の取得で失敗するかどうかと件数は実行タイミングに依存する。
その一方で、失敗を起こす初期化処理は各実行に共通している。**
今回の結果は、実行タイミングで発現する本体の初期化競合という説明を支持する。
単発の実行だけに固有の失敗ではなく、固定した競合条件で同じ経路を繰り返し観測した。
5実行の成功・失敗だけで、将来のすべての実行結果を保証するものではない。

## 固定した条件と実行順

- Windows build 26200.9457、Python 3.11.4（64 bit）、同じ実行ファイル。
- `Downloads/requirements.txt` の106件はすべて一致。前回と同じ追加パッケージがある。
- サンドボックス外で逐次実行し、pytest 自動プラグインは全実行で無効。
- 各組で新しい親 Python を起動し、各条件で3つの新しい spawn worker と新しい出力ディレクトリを使用。
- 各実行は200ラウンド・計600回。barrier で開始を揃え、保護区間の待機は1ミリ秒、取得 timeout は5秒。
- 比較コピーは `_ensure_lock_byte(stream)` 呼出し2か所だけを省いた隔離ソース。
- 条件の実行順を交互に変更。各組の前後に元の履歴保存テストを2回ずつ実行。
- 取得エラーの traceback を全件保存。製品ソース・既存テスト・依存関係の変更なし。

前回の probe は各 worker の代表 traceback を最大3件保存していた。
今回は診断スクリプトに全件保存の選択肢を追加し、5組すべてで同じ設定を使用した。
ロック取得の製品コードは変更していない。開始・終了時の SHA-256 も一致した。
元の履歴テストは3 worker・履歴3件の merge assertion・元の `join(60)` を維持した。

| 実行 | 順序 | 現行コードの取得エラー | 初期化なしの取得エラー |
| --- | --- | --- | --- |
| 1 | 現行 → 初期化なし | 7 / 600 | 0 / 600 |
| 2 | 初期化なし → 現行 | 9 / 600 | 0 / 600 |
| 3 | 現行 → 初期化なし | 7 / 600 | 0 / 600 |
| 4 | 初期化なし → 現行 | 11 / 600 | 0 / 600 |
| 5 | 現行 → 初期化なし | 12 / 600 | 0 / 600 |

現行コードは計3,000回中46回の取得エラーだった。
全46件の traceback が次の同じ経路を示した。

```text
InterProcessFileLock.acquire():97
  → _ensure_lock_byte():165 / stream.flush()
  → PermissionError: [Errno 13] Permission denied
  → InterProcessLockError: cannot acquire inter-process file lock
```

全条件で200ラウンドを完了し、barrier timeout・取得 timeout は発生しなかった。
worker の exitcode はすべて0だった。probe が取得エラーを記録して次のラウンドへ進むためであり、
取得エラー0件を意味しない。共有カウンターは取得成功数と全条件で一致した。
初期化なしの lock は5実行とも0バイトのまま排他を維持した。

## この比較の範囲

OS のスケジューリングやバックグラウンド負荷を完全に固定した試験ではない。
実行順を交互に変えた範囲でも差は維持されたが、完全なランダム化は行っていない。
同じ実行内の取得は相互に影響するため、3,000回を独立した統計標本とは扱わない。
件数は競合を強めた専用試験の観測値で、実利用の失敗率ではない。

元環境の60秒以上続く `Path.open("a+b")` 停止は今回も観測しなかった。
Windows バージョンが元環境と異なるため、その停止の内部原因は今回の比較では確定しない。
今回の繰返し確認は、初期化 flush 失敗についての根拠を追加するものになる。
正式な修正や対応 OS・Python 全体の回帰検証は適用していない。

全 traceback、失敗したラウンド、実行コマンド、順序、環境とソースの hash は
[再現性の証拠 JSON](raw/workflow-history-repeatability-evidence-2026-10-11.json)に保存した。
全ログと隔離コピーは `.runtime/history-repeatability-20261011/` にある。
[再実行スクリプト](probes/history_lock_repeatability.py)は、新しい出力先を指定して使用する。

```powershell
python docs/investigations/quality-security-review-2026-10-06/probes/history_lock_repeatability.py .runtime/history-repeatability-another --pairs 5 --rounds 200
```
