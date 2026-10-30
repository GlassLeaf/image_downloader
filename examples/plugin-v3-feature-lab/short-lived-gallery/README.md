# 短命URLとworkflow

create_image_requestでticketを発行し、模擬サイトの決定的な410にrecover_image_requestで再発行します。check_updatesでfeedを提供し、開始順・workflow再試行・成功画像保持・履歴を確認できます。

[準備・署名・実行・期待結果・異常系・後片付け](../README.md)を先に確認してください。
manifestは署名なしの最小形です。利用者の鍵で署名したコピーをstrictでinstallすることもできます。
このパッケージはpublic v3 APIとPython標準ライブラリだけを使い、他のサンプルをimportしません。

- プラグインID：`local.image-downloader.lab-short-lived-gallery`
- 対象URL：`http://127.0.0.1:<port>/samples/short-lived-gallery/work`
- 作者設定：`origin`と`asset_origin`。prepareが実際のポートで利用者設定を生成します。
- 通常は2章・各2画像を保存します。実サイト／実アカウントは使用しません。
