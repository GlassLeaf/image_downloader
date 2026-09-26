# v3 plugin template

この directory は一つの **site plugin unit** の最小骨格です。directory 名は任意で、
install する source directory は生成済みの `manifest.json`、entry source、author YAML を直接
含む必要がある。

```text
sample-plugin/
  manifest.json
  plugin-metadata.json             # 付属 signer を使う場合だけ
  sample_plugin.py
  sample_plugin.yaml
```

manifest を完成させる経路は二つある。

1. **手作業で署名する場合**: `manifest.json.example` を `manifest.json` に copy して完全な
   manifest と signature を作る。この経路に `plugin-metadata.json` は不要で、`file_tree` にも
   含めない。
2. **付属 signer を使う場合**: `plugin-metadata.json.example` を
   `plugin-metadata.json` に copy して共有 metadata を完成させ、
   `tools/sign_local_site_plugin.py` で `manifest.json` を生成する。この file は signer 入力であり、
   runtime は読まない。

手作業の経路では、`manifest.json.example` を copy しただけでは有効ではない。次をすべて実値に
してから trust/install する。

1. `id` と `publisher` を reverse-DNS identity にする。ID は publisher + `.` で始める。
2. `version`、`kind`、`match_priority`、entry filename/class、author YAML path を実装と
   一致させる。
3. `sample_plugin.py` と `sample_plugin.yaml` を SHA-256 し、`file_tree` を完成する。helper
   file を追加した時もすべて含める。signer 経路で `plugin-metadata.json` を置く場合は、その file
   も含める。
4. canonical `file_tree` JSON の SHA-256 を `file_tree_sha256` に書く。
5. raw Ed25519 public key の Base64 と、その raw bytes の SHA-256 hex を
   `public_key`/`key_id` に書く。
6. canonical inner manifest を Ed25519 private key で署名し、raw signature の Base64 を
   wrapper `signature` に書く。

author YAML は必ず `config` だけを root key に持つ。利用者ごとの設定と secret は plugin
unit に書かず、app config の `plugin_settings.<id>` に置く。`validate_config` は merged
config を同期的・副作用なしで検証し、成功時は `None` を返す。

signer 経路では生成済み `manifest.json` を直接編集しない。metadata、source、author YAML、helper
を更新して signer を再実行する。`plugin-metadata.json` の schema と再署名規則の詳細は
[v3 plugin 開発参照](../../docs/v3/plugin-development-reference.md)を参照する。bundled signer は
`site_plugin` と `image_processor_plugin` の両方を metadata の `kind` に従って署名できる。

この template は package layout と最低限の protocol を示す skeleton であり、完全な
<code>DownloadManifest</code>、API cursor pagination、短命 URL の直前解決・再発行は実装していない。
実装判断は [実行ライフサイクル](../../docs/v3/execution-lifecycle.md) と
[plugin 開発参照](../../docs/v3/plugin-development-reference.md) を正本とする。
