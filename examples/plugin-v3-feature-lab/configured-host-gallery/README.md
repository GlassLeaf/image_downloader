# 設定付きホスト判定

matches_with_configが設定originとURLのscheme/netlocを比較します。不正設定は副作用なくFalseとし、validate_configが説明可能なエラーにします。doctorで一致と不一致を比較できます。

[準備・署名・実行・期待結果・異常系・後片付け](../README.md)を先に確認してください。
manifestは署名なしの最小形です。利用者の鍵で署名したコピーをstrictでinstallすることもできます。
このパッケージはpublic v3 APIとPython標準ライブラリだけを使い、他のサンプルをimportしません。

- プラグインID：`local.image-downloader.lab-configured-host-gallery`
- 対象URL：`http://127.0.0.1:<port>/samples/configured-host-gallery/work`
- 作者設定：`origin`と`asset_origin`。prepareが実際のポートで利用者設定を生成します。
- 通常は2章・各2画像を保存します。実サイト／実アカウントは使用しません。
