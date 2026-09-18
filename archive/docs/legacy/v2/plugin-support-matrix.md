# サイト機構の対応マトリクス v2（履歴資料）

> v3 の plugin selection、context、processor chain は [v3 plugin 作者ガイド](../../../../docs/v3/plugin-author-guide.md) を正本とします。この matrix の v2 descriptor/entry point 記述は現行仕様ではありません。

| サイト機構 | 対応 | v2 pluginの責務 | coreの責務 |
|---|---|---|---|
| 静的HTMLの画像・メタ情報 | 対応 | `inspect()` | request、保存 |
| Referer/Originが必要な画像 | 対応 | `create_image_request()` | retry、並行制御 |
| 複数章catalog API | 対応 | `DownloadManifest`へChapterを構築 | 章・画像並列、結果集約 |
| カーソルAPI | 対応 | `RequestPort`で終端まで取得 | 通信・秘密値境界 |
| CSRFフォームとCookie | 対応 | `AuthFlow` | Cookie永続化、refresh集約 |
| OAuth/Bearer token | 対応 | `AuthFlow` | apply／refreshの再送 |
| HTTP 200の認証失敗 | 対応 | `is_auth_failure()` | 認証retry上限 |
| 短命署名URL | 対応 | `recover_image_request()` | 非認証4xx/5xxで一度だけ回復 |
| サイト固有画像変換 | 対応 | `transform_image()` | 正規化、processor chain、最終形式 |
| JavaScript必須SPA | 非対応 | API直取得の代替がある場合のみ | ブラウザ実行はしない |
| CAPTCHA、アクセス制御の回避 | 非対象 | 実装しない | 実装しない |
| 多要素認証・手動承認 | 非対応 | 実装しない | 実装しない |

multipart、WebSocket、SSEは明示的に非対応です。v1 pluginおよびsidecarのないpluginは、すべての検証モードでimport前に拒否されます。
