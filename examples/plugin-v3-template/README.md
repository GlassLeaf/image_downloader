# v3 plugin template

この directory は一つの **site plugin unit** の最小骨格です。directory 名は任意だが、
install する source directory はこの three files を直接含む必要がある。

```text
sample-plugin/
  manifest.json
  sample_plugin.py
  sample_plugin.yaml
```

`manifest.json.example` を `manifest.json` へ copy しただけでは有効ではない。次をすべて
実値にしてから trust/install する。

1. `id` と `publisher` を reverse-DNS identity にする。ID は publisher + `.` で始める。
2. `version`、`kind`、`match_priority`、entry filename/class、author YAML path を実装と
   一致させる。
3. `sample_plugin.py` と `sample_plugin.yaml` を SHA-256 し、`file_tree` を完成する。helper
   file を追加した時もすべて含める。
4. canonical `file_tree` JSON の SHA-256 を `file_tree_sha256` に書く。
5. raw Ed25519 public key の Base64 と、その raw bytes の SHA-256 hex を
   `public_key`/`key_id` に書く。
6. canonical inner manifest を Ed25519 private key で署名し、raw signature の Base64 を
   wrapper `signature` に書く。

author YAML は必ず `config` だけを root key に持つ。利用者ごとの設定と secret は plugin
unit に書かず、app config の `plugin_settings.<id>` に置く。`validate_config` は merged
config を同期的・副作用なしで検証し、成功時は `None` を返す。

詳細は [v3 plugin 作者ガイド](../../docs/v3/plugin-author-guide.md) と
[v3 信頼・配布・運用](../../docs/v3/trust-and-operations.md) を参照する。
