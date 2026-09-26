# Tutorial: first plugin

plugin template は directory layout と最小 class contract の skeleton である。署名済みの完成 package にする手順は [plugin package reference](../reference/plugin-package.md)、hook の正確な型は [plugin hook reference](../reference/plugin-hooks.md) を使用する。

1. [plugin template](../../../examples/plugin-v3-template) から unit を作る。
2. manifest の `config_file` が指す author-default YAML（template では `sample_plugin.yaml`）の root を `config:` だけにする。
3. `validate_config`、`matches`、`inspect`、`create_image_request` を実装する。
4. source tree を署名し、temporary plugin root へ strict install して `doctor --json` を実行する。

template は最小 skeleton であり、cursor pagination、CSRF/OAuth、短命 URL、processor transform の完成例ではない。これらは [plugin source examples](../reference/plugin-hooks.md#plugin-examples) を比較対象にする。source unit を更新したら、[signing workflow](../reference/plugin-package.md#plugin-signing-workflow) に従って manifest の tree と signature を再生成する。
