<a id="tutorial-first-plugin"></a>

# チュートリアル：初めてのプラグイン

プラグインのテンプレートは、ディレクトリ構成と最小限のクラス契約を示す雛形です。入口のソースには、すべてのフックに安定した公開型の注釈があり、実装を始める際に入出力を確認できます。署名済みの完成パッケージにする手順は[プラグインパッケージリファレンス](../reference/plugin-package.md)、フックの意味・検証・例外は[プラグインフックリファレンス](../reference/plugin-hooks.md)を参照してください。

1. [プラグインのテンプレート](../../../examples/plugin-v3-template)から、プラグイン一式を作ります。
2. manifestの`config_file`が指す作者既定値のYAML（テンプレートでは`sample_plugin.yaml`）のルートキーを`config:`だけにします。
3. `validate_config`、`matches`、`inspect`、`create_image_request`を実装します。
4. ソースツリーを署名し、一時的なプラグインルートへstrictモードでインストールして、`doctor --json`を実行します。

テンプレートは最小限の雛形であり、カーソルによるページ分割、CSRF・OAuth、短命URL、プロセッサーの画像変換の完成例ではありません。これらは[プラグインの実装例](../reference/plugin-hooks.md#plugin-examples)と比較してください。ソースを更新したら、[署名手順](../reference/plugin-package.md#plugin-signing-workflow)に従ってmanifestのツリーと署名を再生成します。

空結果の成功を制御したい場合は、[空の結果の許可](../reference/plugin-hooks.md#empty-result-permissions)
に従い、`DownloadManifest.allow_empty_chapters`と各`Chapter.allow_empty_images`を指定する。
未指定は従来動作を維持する。章一覧0件には既存設定の拒否が優先し、画像0件は章ごとに判断する。
新フィールドを使う本体の対応確認と、変更したプラグインの再署名も行う。
