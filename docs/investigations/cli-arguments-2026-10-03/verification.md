# 実装・検証記録

この記録は初回実装の検証結果。後続の追加確認は[定義拡張の確認記録](extension-verification.md)、削除・変更の修正と最新の検証結果は[削除・変更の確認記録](reduction-verification.md)に記載している。

確定したP1・P2を実装した。エラーとヘルプの文面は英語で、成功時JSON、既存終了コード、文書化された効果なし指定とlegacy Cookie指定を維持している。

| 優先度 | 実装内容 | 主な箇所 |
| --- | --- | --- |
| P1 | `ArgumentError(ConfigurationError)` と安全な内部診断を追加。CLIの通常出力・JSONを同じ診断から生成。共有 `ErrorInfo` は変更しない | `exceptions.py`, `diagnostics.py`, `commands/dispatch.py` |
| P1 | 35オプションの型・既定値・反復・適用範囲・効果なし指定・競合を一元化。明示指定・順序を記録し、省略名を無効化 | `commands/options.py`, `commands/parser.py` |
| P1 | 設定読み込み前の適用範囲・競合・CLI値検証。絶対HTTP(S) URL、既定値の明示、空値、階層をまたぐ競合を検証 | `commands/validation.py` |
| P1 | JSONファイルを検証時に一度だけ読み、実行時にも検証済み内容を使用。ファイル指定を先、inline指定を後にマージ | `commands/setup.py`, `commands/dispatch.py` |
| P1 | 設定の不存在、YAML構文、値の制約、レイヤー違反を区別。項目・条件・レイヤー・ファイル名・取得可能な行と列を保持し、値・絶対パス・YAML抜粋を追加診断へ出さない | `configuration/layers.py`, `diagnostics.py` |
| P2 | config/plugin/stateの正式な階層と必須引数を定義。互換用 `command_args` を構成 | `commands/parser.py` |
| P2 | `help` と対象階層のヘルプを追加。JSON指定時もテキスト表示し、設定・プラグイン・Cookie処理へ進まない | `commands/parser.py` |
| P2 | CLIリファレンス、公開例外一覧、API inventory、変更履歴・JSON移行例、既存文書の契約証拠を更新 | `docs/v3/reference/`, `docs/v3/maintenance/` |

## ローカル検証

環境はWindows、Python 3.11.4、既存の `.runtime/quality-venv`。最終実装で以下を確認した。

| 確認 | 結果 |
| --- | --- |
| 新しい引数診断テスト | 118 passed |
| CLI・設定・公開API・文書の重点確認 | 337 passed, 2 skipped（空値の追加9件はその後の診断テストと全件実行で確認） |
| 全v3テスト `python -m pytest -q --cov=image_downloader` | 821 passed, 2 skipped |
| カバレッジ | 86.85%（基準75%） |
| `python -m ruff check .` | 通過 |
| `python -m mypy src/image_downloader` | 83 source filesで通過 |
| `python -m pip check` | No broken requirements found |
| 元ケース再評価 | 8,794件、元ID・引数順序を保持 |
| 有効URLへの置換試験 | 3,593件、別ファイルに保存 |
| 独立モジュール実行との照合 | 引数不正・ヘルプ23件すべて一致 |

全件カバレッジ実行で既存の共有履歴テストが子プロセス待機15秒で失敗したため、テストの待機を60秒に延長した。製品側のロック期限30秒、ロック処理、保存処理は変更していない。子プロセスの終了コードと3件の保存結果を確認するassertionは維持している。変更後の全件実行は通過した。

再評価は一つの公開パーサーを、新しいNamespaceで繰り返し利用して高速化した。配置・値不足・JSON・ヘルプ等の代表23件は、パーサーを再利用しない実際の `python -m image_downloader` と出力・終了コードを照合している。境界停止を必要とする有効入力はモジュール実行へ渡していない。

設定解決は読み取り専用であり、ダウンロード・プラグイン操作・Cookie操作は実行直前で停止した。`validation_passed` は検証通過で、実行成功ではない。config path/init/profile init は専用処理の前で停止し、設定作成成功も判定していない。

## CIの確認範囲

既存 `.github/workflows/quality.yml` はWindows・Linux・macOS、Python 3.11〜3.14の12構成を維持している。このホストにはWindows/Python 3.11のみがあるため、他OS・PythonのCIジョブは実行していない。ローカルの通過をCI全構成の通過とは扱わない。

## 比較資料と再実行

- [修正前後の比較レポート](report.md)
- [全8,794件の比較一覧](before-after-comparison.csv)
- [有効URLの比較一覧](valid-url-comparison.csv)
- [元資料のSHA-256と分類集計](comparison-summary.json)
- [公開インターフェースの移行例](../../v3/maintenance/cli-diagnostics-migration.md)

```powershell
python tools/audit_cli_arguments.py --replay docs/investigations/cli-arguments-2026-10-02/raw-results.json --output docs/investigations/cli-arguments-2026-10-03/invalid-input-replay --execution-boundary --reuse-parser
python tools/audit_cli_arguments.py --replay docs/investigations/cli-arguments-2026-10-02/raw-results.json --output docs/investigations/cli-arguments-2026-10-03/valid-url-probes --execution-boundary --valid-url-probes --reuse-parser
python tools/render_cli_repair_comparison.py
```

元資料と同じ出力先へのreplayをスクリプトが拒否する。元のexpected_hintは調査時の改善案であり、実行成功の独立した判定基準として使用していない。
