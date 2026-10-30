# 画像単位の保存指定

ImageSaveOptionsでJPEG/PNG・品質・圧縮・EXIFを指定します。original_filenameとoutput_format_valuesも提供します。実行には生成されたoutput.yamlを使うと、独自tokenがファイル名へ反映されます。

[準備・署名・実行・期待結果・異常系・後片付け](../README.md)を先に確認してください。
manifestは署名なしの最小形です。利用者の鍵で署名したコピーをstrictでinstallすることもできます。
このパッケージはpublic v3 APIとPython標準ライブラリだけを使い、他のサンプルをimportしません。

- プラグインID：`local.image-downloader.lab-output-gallery`
- 対象URL：`http://127.0.0.1:<port>/samples/output-gallery/work`
- 作者設定：`origin`と`asset_origin`。prepareが実際のポートで利用者設定を生成します。
- 通常は2章・各2画像を保存します。実サイト／実アカウントは使用しません。
