# local plugin API v3 ドキュメント

この directory は local plugin API v3 の正本です。v3 は v2 と互換性のない
local-directory plugin API であり、v1/v2 の entry point、wheel sidecar、旧設定
tree を検索・読込しません。

## 読み始める場所

| 読者 | 文書 | 目的 |
|---|---|---|
| 利用者・運用者 | [設定と CLI](configuration-and-cli.md) | 設定 tree、優先順位、`doctor`、日常の実行 |
| plugin 作者 | [plugin 作者ガイド](plugin-author-guide.md) | directory 構造、Python contract、設定・secret・context |
| HTTP/認証を扱う plugin 作者 | [サイト plugin 統合・認証ガイド](site-plugin-integration-guide.md) | secret、request、AuthFlow、Cookie、回復、並列制御、対象外機能 |
| 管理者 | [信頼・配布・運用](trust-and-operations.md) | manifest、署名、catalog、install/trust/revoke、診断 |
| 組込み利用者 | [ライブラリ API](library-api.md) | `RuntimeComposer`、service、operation override |
| 保守・移行担当 | [テストと移行](testing-and-migration.md) | v3 の受入基準、v2 からの手動移行 |
| 公開担当 | [公開ベータ確認事項](release-checklist.md) | 配布物検証、ライセンスと公開停止条件 |

雛形は [`examples/plugin-v3-template`](../../examples/plugin-v3-template) にある。
`manifest.json.example` は説明用であり、hash と Ed25519 signature を実値へ
置き換えるまで install/trust できない。

短い仕様概要は [`docs/plugin-api-v3.md`](../plugin-api-v3.md) に残す。日常操作、公開 API、
受入条件の正本はこの directory 内の分割文書である。

## 仕様の優先順位

1. この `docs/v3/` の文書。
2. 公開 package root の型・例外・実行 contract。
3. `tests/v3/` の受入テスト。

実装やテストがこの文書と異なる場合は、差異を bug として扱い、どちらを修正
するかを明示して整合させる。`archive/docs/legacy/` の v1/v2 文書は履歴参照用で、現行仕様の
根拠にしてはならない。

## v3 の全体像

```text
app.yaml + profile/site overlays + CLI app override
                 │
                 ▼
             AppConfig ──► RuntimeComposer(config_root, plugin_root)
                                  │
             plugin_root/catalog.json + signed plugin directories
                                  │
                                  ▼
          discovery → verification → selection → operation validation
                                  │
                                  ▼
             site plugin → image processor chain → output/state
```

plugin は同じ Python process で実行される。署名と catalog は信頼する plugin を
選別する仕組みであり、sandbox や dependency の自動導入ではない。plugin は追加の
dependency を自動導入せず、HTTP、secret、filesystem などの core capability を
context で与えられた範囲でのみ利用する。

## 重要な互換性境界

- 旧 `plugins.<id>` 設定は廃止。bootstrap の `plugins.root` は現行キーで、利用者 plugin 設定は `plugin_settings:`。
- `security.plugin_catalog` は廃止。catalog の場所は常に
  `<plugin-root>/catalog.json`。
- plugin の ID/priority を class descriptor に書かない。署名済み manifest が正本。
- profile overlay や site overlay の `profile:` はエラー。profile は main `app.yaml`
  または `--profile` で一度だけ選択される。
- 旧 profile config/data、v2 entry point、旧 sidecar は自動移行しない。移行手順は
  [テストと移行](testing-and-migration.md) を参照する。
