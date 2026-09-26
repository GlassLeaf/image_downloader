# Migration

v3 は v1/v2 entry point、descriptor、wheel sidecar、legacy configuration tree を discovery/compatibility read しない。adapter も提供しない。

1. site/processor ごとに v3 directory unit を作り、kind を混在させない。
2. descriptor ID/priority を manifest、author default を `plugin.yaml` の `config:`、user setting を `plugin_settings.<id>.config` へ移す。raw secret は external reference にする。
3. app/global/site/profile YAML を v3 layer に手動配置し、profile/site layer から bootstrap fields を除く。
4. new data root の profile directory へ必要な旧 data を確認して copy する。旧 path fallback はない。
5. tree digest、manifest digest、Ed25519 signature を作り直し、strict install と `doctor --json` で検証する。
6. plugin root/catalog を backup し、secret reference を再解決し、update-state migration を確認する。

v2/v3 を同じ root で同時 discovery できない。新 root で strict validation を完了してから runtime の plugin root を切り替える。update state の schema/migration semantics は [runtime behavior reference](../reference/runtime-behavior.md#runtime-update-state) が正本である。
