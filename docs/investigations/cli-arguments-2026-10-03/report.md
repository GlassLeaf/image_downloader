# CLI 引数修正後の比較 (2026-10-03)

元の8,794件を同じID・引数順序で再評価した。元資料は変更していない。
非URL値を使った元ケースと、有効な絶対HTTP(S) URLへ置換した3,593件を分離した。
有効入力は設定を読み取り専用で解決した後、ハンドラー実行の直前で停止した。
config path/init/profile init はその専用処理の前で停止しているため、設定作成の成功も判定していない。
`validation_passed` は「検証通過」を表し、ダウンロード・プラグイン操作・Cookie操作の実行成功を意味しない。

- [全8,794件の修正前後比較](before-after-comparison.csv)
- [有効URL置換3,593件の比較](valid-url-comparison.csv)
- [元の調査結果](../cli-arguments-2026-10-02/report.md)
- [分類集計と元資料のSHA-256](comparison-summary.json)
- [実装・検証記録](verification.md)

| 引数の組み合わせ・順序 | 修正前 | 修正後 |
| --- | --- | --- |
| `--list` | error [configuration_error]: configuration is invalid | error [argument_error]: unrecognized option: --list |
| `--unknown-option` | __main__.py: error: unrecognized arguments: --unknown-option | error [argument_error]: unrecognized option: --unknown-option |
| `help` | error [plugin_error]: plugin operation failed | help displayed |
| `help plugin` | __main__.py: error: unrecognized arguments: plugin | help displayed |
| `(引数なし)` | error [configuration_error]: configuration is invalid | error [argument_error]: a command or absolute HTTP(S) URL is required |
| `--host example.test plugin list` | error [configuration_error]: configuration is invalid | error [argument_error]: --host is not valid for plugin list |
| `plugin list --host example.test` | __main__.py: error: unrecognized arguments: --host example.test | error [argument_error]: --host is not valid for plugin list |

## 修正後の分類

| 分類 | 元ケース | 有効URL置換 |
| --- | ---: | ---: |
| argument_error | 7860 | 2343 |
| configuration_error | 2 | 24 |
| help | 255 | 27 |
| validation_passed | 677 | 1199 |

独立したモジュール実行による照合: 23/23件一致。
モジュール照合は引数不正・ヘルプのみで、境界停止が必要な有効入力は通常のモジュール実行へ渡していない。
元の expected_hint は調査時の改善案として保存しており、実行成功の独立した判定基準ではない。
