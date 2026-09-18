# サイト別 plugin 設計パターン v2（履歴資料）

以下の 6 構成は、通常の entry point には登録しない架空ドメイン向けの v2 参考実装である。source は
[`examples/legacy/v2/plugins`](../../../../examples/legacy/v2/plugins)、固定 HTML/JSON fixture は
[`examples/legacy/v2/fixtures`](../../../../examples/legacy/v2/fixtures)、履歴 test は
[`tests/legacy/v2/test_site_plugin_examples.py`](../../../../tests/legacy/v2/test_site_plugin_examples.py) にある。
現在の checkout で実行可能であることは保証しない。

| 構成 | `inspect()` | request／認証 | 更新 |
|---|---|---|---|
| 公開HTMLギャラリー | HTMLからtitleと画像を抽出 | Referer/Originを`create_image_request()`へ集約 | 任意 |
| 複数章catalog API | APIから複数Chapterを構築 | 通常request | content IDとrevisionをsnapshotへ |
| カーソルAPI | `RequestPort`で終端まで反復 | `SecretProvider`からAPI秘密値を取得 | 任意 |
| CSRFログイン | 保護ページをmanifestへ変換 | `AuthFlow.refresh()`でlogin GET/CSRF/POST | 任意 |
| OAuth media API | JSON media APIをmanifestへ変換 | HTTP 200認証失敗も`is_auth_failure()`で判定 | revisionをsnapshotへ |
| 署名CDN | 安定image IDをmanifestへ保存 | `recover_image_request()`で一度だけ再署名 | 任意 |

各例は`PluginExecutionContext`の`config`、`secrets`、`requests.execute()`だけを使い、Cookie jar、filesystem、logger、schedulerを操作しません。fixtureのcredential・token・URLは全てテスト専用です。実在site向けpluginでは、[plugin-testing-guide.md](plugin-testing-guide.md)の受入条件を追加してから配布してください。
