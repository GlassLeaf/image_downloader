# ドキュメント索引

## 現行仕様: API v3

現行仕様の正本は[API v3](v3/README.md)です。利用、プラグイン開発、HTTPのライフサイクル、ライブラリAPI、配布・運用、テスト・公開の入口を用途別に整理しています。

| 読者・目的 | 文書 |
|---|---|
| 全体像と仕様の優先順位 | [API v3の索引](v3/README.md) |
| 利用者・運用者 | [CLIリファレンス](v3/reference/cli.md)と[設定リファレンス](v3/reference/configuration.md) |
| プラグイン作者 | [パッケージリファレンス](v3/reference/plugin-package.md)と[フックリファレンス](v3/reference/plugin-hooks.md) |
| フックと動的URLを扱うプラグイン作者 | [実行ライフサイクル](v3/explanation/execution-lifecycle.md) |
| 組込み利用者 | [ライブラリAPI](v3/reference/library-api.md) |
| 安定した公開APIの全一覧 | [ライブラリAPI](v3/reference/library-api.md)と[公開契約一覧](v3/reference/api-contract-inventory.md) |
| 信頼・配布・管理者 | [パッケージの信頼検証](v3/reference/plugin-package.md)と[実行時の運用](v3/reference/runtime-behavior.md) |
| 保守・公開担当 | [検証・移行・リリース](v3/maintenance/testing.md) |
| 旧v3文書の移管先 | [対応台帳](v3/maintenance/legacy-coverage.md) |
| 雛形 | [プラグインのテンプレート](../examples/plugin-v3-template) |

## 履歴資料

[v3再編前のスナップショット](../archive/docs/legacy/v3-pre-reorg/README.md)には、再編前のv3文書を比較用に保存しています。現行仕様ではありません。

[v2のアーカイブ](../archive/docs/legacy/v2/README.md)には、v2の文書・サンプル・fixture・旧テストを保存しています。現在のチェックアウトで実行できることは保証しません。v3はv2のエントリーポイント・記述子・wheelの補助ファイル・旧設定ツリーを読まないため、新規プラグインや運用には[検証・移行・リリース](v3/maintenance/testing.md)を参照してください。

[v1のアーカイブ](../archive/docs/legacy/v1/README.md)は、削除済みAPIの境界だけを記録しています。
