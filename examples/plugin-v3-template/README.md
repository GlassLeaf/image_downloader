<a id="v3-plugin-template"></a>

# v3プラグインのテンプレート

このディレクトリは、1つの**サイトプラグイン**の最小限の雛形です。ディレクトリ名は任意です。インストールするソースディレクトリには、生成済みの`manifest.json`、入口のソース、作者既定値のYAMLを直接配置してください。

```text
sample-plugin/
  manifest.json
  plugin-metadata.json             # 付属 signer を使う場合だけ
  sample_plugin.py
  sample_plugin.yaml
```

manifestを完成させる方法は2つあります。

1. **手作業で署名する場合**：`manifest.json.example`を`manifest.json`へコピーし、完全なmanifestと署名を作ります。この方法では`plugin-metadata.json`は不要であり、`file_tree`にも含めません。
2. **付属の署名ツールを使う場合**：`plugin-metadata.json.example`を`plugin-metadata.json`へコピーして共通メタデータを完成させ、`tools/sign_local_site_plugin.py`で`manifest.json`を生成します。メタデータファイルは署名ツールへの入力であり、実行時には読みません。

手作業で署名する場合は、`manifest.json.example`をコピーするだけでは有効になりません。次をすべて実際の値に置き換えてから、trust・installを行ってください。

1. `id`と`publisher`を逆DNS形式の識別子にします。IDはpublisherと`.`を連結した文字列で始めます。
2. `version`、`kind`、`match_priority`、入口のファイル名・クラス、作者既定値のYAMLのパスを、実装と一致させます。
3. `sample_plugin.py`と`sample_plugin.yaml`のSHA-256を求め、`file_tree`を完成させます。補助ファイルを追加した場合もすべて含めます。署名ツール用に`plugin-metadata.json`を置く場合は、そのファイルも含めます。
4. 正規化した`file_tree`のJSONのSHA-256を、`file_tree_sha256`へ設定します。
5. Ed25519公開鍵の生バイト列をBase64化した値と、そのバイト列のSHA-256の16進表記を、`public_key`・`key_id`へ設定します。
6. 正規化した内側のmanifestをEd25519秘密鍵で署名し、署名の生バイト列をBase64化して、外側の`signature`へ設定します。

作者既定値のYAMLは、必ず`config`だけをルートキーにします。利用者ごとの設定と秘密情報はプラグイン内に書かず、アプリ設定の`plugin_settings.<id>`へ置きます。`validate_config`はマージ済みの設定を同期的かつ副作用なしで検証し、成功時は`None`を返します。

署名ツールを使う場合は、生成済みの`manifest.json`を直接編集しないでください。メタデータ、ソース、作者既定値のYAML、補助ファイルを更新してから、署名ツールを再実行します。`plugin-metadata.json`のスキーマと再署名の規則は[プラグインパッケージリファレンス](../../docs/v3/reference/plugin-package.md#plugin-signer-metadata)、フックの実装は[プラグインフックリファレンス](../../docs/v3/reference/plugin-hooks.md)を参照してください。`sample_plugin.py`の型注釈は、実装開始時に各フックの入出力を確認するためのものです。フックの意味・検証・例外はリファレンスを正本とします。付属の署名ツールは、メタデータの`kind`に応じて`site_plugin`と`image_processor_plugin`の両方を署名できます。

このテンプレートは、パッケージ構成と最低限のプロトコルを示す雛形です。完全な<code>DownloadManifest</code>、APIのカーソルによるページ分割、短命URLの直前解決・再発行は実装していません。実装上の判断には[実行ライフサイクル](../../docs/v3/explanation/execution-lifecycle.md)と[プラグイン開発リファレンス](../../docs/v3/reference/plugin-hooks.md)を正本として使ってください。
