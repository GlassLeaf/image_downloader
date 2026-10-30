# 本体Cookie jarによる認証

プラグインはブラウザもCookieファイルも読みません。本体jarが付与するCookieで模擬サイトにアクセスします。人工jarの自動テストと、隔離環境での実Chrome試験を分離します。

永続化先は試験専用データルートの`profiles/<profile>/cookie/cookies.v2.enc`です。資格情報と同じ保管庫のマスターキーで保護し、旧`cookies.enc`は読み取りません。Cookieを取り込んだときと同じデータルート・プロファイルで実行してください。

[準備・署名・実行・期待結果・異常系・後片付け](../README.md)を先に確認してください。
manifestは署名なしの最小形です。利用者の鍵で署名したコピーをstrictでinstallすることもできます。
このパッケージはpublic v3 APIとPython標準ライブラリだけを使い、他のサンプルをimportしません。

- プラグインID：`local.image-downloader.lab-cookie-gallery`
- 対象URL：`http://127.0.0.1:<port>/samples/cookie-gallery/work`
- 作者設定：`origin`と`asset_origin`。prepareが実際のポートで利用者設定を生成します。
- 通常は2章・各2画像を保存します。実サイト／実アカウントは使用しません。

[実Chromeの手動試験](../../../manual-tests/browser-cookies/README.md)はAI・CIでは実行しません。
