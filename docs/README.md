# ドキュメント索引

## 現行仕様: API v3

現行の正本は [API v3](v3/README.md) です。利用、plugin authoring、HTTP lifecycle、
library API、配布・運用、テスト・公開の入口を用途別に整理しています。

| 読者・目的 | 文書 |
|---|---|
| 全体像と仕様の優先順位 | [API v3 index](v3/README.md) |
| 利用者・運用者 | [CLI 参照](v3/reference/cli.md) と [設定参照](v3/reference/configuration.md) |
| plugin 作者 | [package 参照](v3/reference/plugin-package.md) と [hook 参照](v3/reference/plugin-hooks.md) |
| hook と動的 URL を扱う plugin 作者 | [実行ライフサイクル](v3/explanation/execution-lifecycle.md) |
| 組込み利用者 | [ライブラリ API](v3/reference/library-api.md) |
| 全 stable export | [ライブラリ API](v3/reference/library-api.md) と [contract inventory](v3/reference/api-contract-inventory.md) |
| 信頼・配布・管理者 | [package trust](v3/reference/plugin-package.md) と [runtime operations](v3/reference/runtime-behavior.md) |
| 保守・公開担当 | [検証・移行・リリース](v3/maintenance/testing.md) |
| 旧 v3 記述の移管先 | [coverage ledger](v3/maintenance/legacy-coverage.md) |
| 雛形 | [examples/plugin-v3-template](../examples/plugin-v3-template) |

## 履歴資料

[v3 pre-reorg snapshot](../archive/docs/legacy/v3-pre-reorg/README.md) は、再編前の v3 文書を比較用に保存している。
現行仕様ではない。

[v2 archive](../archive/docs/legacy/v2/README.md) には v2 の文書、サンプル、fixture、非現行 test を保存している。
これらは現在の checkout で実行可能であることを保証しない。v3 は v2 entry point、descriptor、
wheel sidecar、旧 config tree を読まないため、新規 plugin や運用には
[検証・移行・リリース](v3/maintenance/testing.md) を使用する。

[v1 archive](../archive/docs/legacy/v1/README.md) は削除済み API の境界だけを記録する。
