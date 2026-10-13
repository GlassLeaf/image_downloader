# Tutorial: first plugin

plugin template は directory layout と最小 class contract の skeleton である。entry source には全 hook の stable 型注釈があるため、実装開始時の input/output の即時参照になる。署名済みの完成 package にする手順は [plugin package reference](../reference/plugin-package.md)、hook の意味、検証、例外は [plugin hook reference](../reference/plugin-hooks.md) を使用する。

1. [plugin template](../../../examples/plugin-v3-template) から unit を作る。
2. manifest の `config_file` が指す author-default YAML（template では `sample_plugin.yaml`）の root を `config:` だけにする。
3. `validate_config`、`matches`、`inspect`、`create_image_request` を実装する。
4. source tree を署名し、temporary plugin root へ strict install して `doctor --json` を実行する。

template は最小 skeleton であり、cursor pagination、CSRF/OAuth、短命 URL、processor transform の完成例ではない。これらは [plugin source examples](../reference/plugin-hooks.md#plugin-examples) を比較対象にする。source unit を更新したら、[signing workflow](../reference/plugin-package.md#plugin-signing-workflow) に従って manifest の tree と signature を再生成する。

空結果の成功を制御したい場合は、[empty-result permissions](../reference/plugin-hooks.md#empty-result-permissions)
に従い、`DownloadManifest.allow_empty_chapters`と各`Chapter.allow_empty_images`を指定する。
未指定は従来動作を維持する。章一覧0件には既存設定の拒否が優先し、画像0件は章ごとに判断する。
新フィールドを使う本体の対応確認と、変更したプラグインの再署名も行う。
