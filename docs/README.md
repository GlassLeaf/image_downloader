# ドキュメント索引

## 現行仕様: local plugin API v3

現行の正本は [`docs/v3/`](v3/README.md) です。設定、plugin directory、signature/catalog、
CLI、library API、テスト、移行は v3 文書だけを基準にしてください。

| 読者・目的 | 文書 |
|---|---|
| 全体像と仕様の優先順位 | [v3 index](v3/README.md) |
| 利用者・運用者 | [設定と CLI](v3/configuration-and-cli.md) |
| plugin 作者 | [plugin 作者ガイド](v3/plugin-author-guide.md) |
| HTTP/認証を扱う plugin 作者 | [サイト plugin 統合・認証ガイド](v3/site-plugin-integration-guide.md) |
| 信頼・配布・管理者 | [信頼・配布・運用](v3/trust-and-operations.md) |
| 組込み利用者 | [ライブラリ API](v3/library-api.md) |
| 保守・テスト・移行 | [テストと移行](v3/testing-and-migration.md) |
| 公開担当 | [公開ベータ確認事項](v3/release-checklist.md) |
| 雛形 | [examples/plugin-v3-template](../examples/plugin-v3-template) |

短い仕様概要は [plugin-api-v3.md](plugin-api-v3.md) に残しているが、具体的な操作と
受入条件は上の分割文書を参照する。

## 履歴資料

[v2 archive](../archive/docs/legacy/v2/README.md) には v2 の文書、サンプル、fixture、非現行 test を保存している。
これらは現在の checkout で実行可能であることを保証しない。v3 は v2 entry point、descriptor、
wheel sidecar、旧 config tree を読まないため、新規 plugin や運用には
[テストと移行](v3/testing-and-migration.md) を使用する。

[v1 archive](../archive/docs/legacy/v1/README.md) は削除済み API の境界だけを記録する。
