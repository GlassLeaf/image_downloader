# keyringと環境変数の参照

context.secrets.get("api_token")をAuthFlowでBearerヘッダーに設定します。登録・削除と未設定時の確認は総合手順のkeyring節を参照してください。

[準備・署名・実行・期待結果・異常系・後片付け](../README.md)を先に確認してください。
manifestは署名なしの最小形です。利用者の鍵で署名したコピーをstrictでinstallすることもできます。
このパッケージはpublic v3 APIとPython標準ライブラリだけを使い、他のサンプルをimportしません。

- プラグインID：`local.image-downloader.lab-credential-gallery`
- 対象URL：`http://127.0.0.1:<port>/samples/credential-gallery/work`
- 作者設定：`origin`と`asset_origin`。prepareが実際のポートで利用者設定を生成します。
- 通常は2章・各2画像を保存します。実サイト／実アカウントは使用しません。
- 秘密情報名：`api_token`、参照名：`LAB_TOKEN`。値は架空の`lab-token`だけを使用します。
