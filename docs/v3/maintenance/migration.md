# Migration

v3 は v1/v2 entry point、descriptor、wheel sidecar、legacy configuration tree を discovery/compatibility read しない。adapter も提供しない。

1. site/processor ごとに v3 directory unit を作り、kind を混在させない。
2. descriptor ID/priority を manifest、author default を `plugin.yaml` の `config:`、user setting を `plugin_settings.<id>.config` へ移す。raw secret は external reference にする。
3. app/global/site/profile YAML を v3 layer に手動配置し、profile/site layer から bootstrap fields を除く。
4. new data root の profile directory へ必要な旧 data を確認して copy する。旧 path fallback はない。
5. tree digest、manifest digest、Ed25519 signature を作り直し、strict install と `doctor --json` で検証する。
6. plugin root/catalog を backup し、secret reference を再解決し、update-state migration を確認する。

v2/v3 を同じ root で同時 discovery できない。新 root で strict validation を完了してから runtime の plugin root を切り替える。update state の schema/migration semantics は [runtime behavior reference](../reference/runtime-behavior.md#runtime-update-state) が正本である。

<a id="plugin-secret-environment-migration"></a>

## QS-016：プラグイン秘密参照の環境変数名

QS-016 の修正では接頭辞 `IMAGE_DOWNLOADER_PLUGIN_` を維持し、名前を `IMAGE_DOWNLOADER_PLUGIN_<ID_HEX>_<REFERENCE_HEX>` に統一する。両フィールドは `value.encode("utf-8", "surrogatepass").hex().upper()` による完全な符号化で、アプリの版更新だけでは名前を変更しない。旧名は読まず、fallback もしない。

設定 schema、ID・reference の検証規則、keyring service/username、公開 API は変わらない。keyring のみの利用者は移行不要で、プラグインの再署名も不要である。環境変数の利用者は次の手順で移行する。

1. 設定にある plugin ID と reference から、下の例で旧名と新名を確認する。logical secret name ではなく、対応する reference を使う。
2. 旧名の定義元と値の所有者を確認し、その値を正しい新名へ移す。旧名が衝突していた場合、曖昧な旧値を複数 plugin へ自動コピーしない。
3. 新名で認証・取得できることを確認する。未設定・空値は keyring に進むため、認証成功だけで環境変数移行の成功と判断せず、新名の定義も確認する。通常の診断に秘密値・reference を追加しない。
4. 他の plugin/profile や別プロセスで使用していないことを確認した旧名を、User/System 環境、shell 設定、`.env`、CI 設定などの**定義元から削除**する。プロセスを再起動し、旧名が再注入されないことを確認する。

以下の Python 例は**名前だけ**を生成する。環境変数の値や credential store を読み書きしない。

```python
plugin_id = "com.example.a-b"
reference = "TOKEN"
prefix = "IMAGE_DOWNLOADER_PLUGIN_"

# Former rule: identify the migration source without reading its value.
legacy_id = "".join(char if char.isalnum() else "_" for char in plugin_id).upper()
legacy_reference = "".join(char if char.isalnum() else "_" for char in reference).upper()
old_name = f"{prefix}{legacy_id}_{legacy_reference}"

id_hex = plugin_id.encode("utf-8", "surrogatepass").hex().upper()
reference_hex = reference.encode("utf-8", "surrogatepass").hex().upper()
new_name = f"{prefix}{id_hex}_{reference_hex}"

print("old name:", old_name)
print("new name:", new_name)
```

この例の出力は次になる。名前の確認は移行作業用であり、アプリの通常ログや共有診断へ追加するものではない。

```text
old name: IMAGE_DOWNLOADER_PLUGIN_COM_EXAMPLE_A_B_TOKEN
new name: IMAGE_DOWNLOADER_PLUGIN_636F6D2E6578616D706C652E612D62_544F4B454E
```

アプリによる自動削除や、接頭辞全体の一括削除は行わない。他の profile や別プロセスでの使用をアプリだけでは判断できないため、削除対象は利用者が確認した旧名に限定する。現在の shell から消すだけでは、親プロセスや永続設定から再注入される場合がある。定義元の整理とプロセスの再起動まで確認する。
