<a id="tutorial-first-download"></a>

# チュートリアル：初めてのダウンロード

固定のユーザー設定を作り、ダウンロードを1回成功させるための最短手順です。オプションの完全な適用規則は[CLIリファレンス](../reference/cli.md)、設定値は[設定リファレンス](../reference/configuration.md)を参照してください。

```powershell
python -m pip install .
image-downloader config path
image-downloader config init `
  --data-root "D:\Images\image-downloader" `
  --plugin-root "C:\Users\you\AppData\Local\image-downloader\plugins"
image-downloader download "https://example.test/gallery"
image-downloader doctor --json
```

`config init`は、明示した絶対パスを含む固定のユーザー設定を作成します。`download`、更新一覧の取得、Cookie操作、プラグインの変更操作も、設定がなければ初期設定を作成する場合があります。`doctor`、`config path`、`config explain`、plugin listは読取り専用です。

失敗を自動処理する場合は、次に[CLIのJSON出力](../reference/cli.md#cli-json)と[終了コード](../reference/cli.md#cli-exit-status)を参照してください。

更新チェックに対応したプラグインでは、チェックと取得を一度に実行できる。

```powershell
image-downloader workflow "https://example.test/feed"
image-downloader workflow "https://example.test/feed" --download-scope all --existing-file skip
image-downloader workflow "https://example.test/feed" --download-scope updated --json
```

既定の `updated` は初回に全件を取得し、以降は追加・変更・未完了の対象を取得する。
`all` は今回一覧に存在する全URLを毎回処理する。既存ファイルへの対応は通常の設定に従う。
一覧から消えた対象の保存済み画像は削除しない。

画像は通常downloadと同じ`<data_root>/profiles/<profile名>/downloads`に保存する。
`--output-dir`で画像の保存先を変えても、workflowの予定・逐次ログは同じprofileの`logs/workflow`に残る。
予定URLと実際に処理を始めたURLの区別、画像のHTTP要求ログは
[ログの保存先とURL](../reference/logging.md#workflow-output-locations)を参照する。
pluginエラーによる継続／停止、再試行、キャンセル時の結果は
[workflowのFAQ](../how-to/faq.md)から確認できる。
