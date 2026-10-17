<a id="migration"></a>

# 移行手順

v3は、v1・v2のエントリーポイント、記述子、wheelの補助ファイル、旧設定ツリーを検出・互換読込みしません。アダプターも提供しません。

1. サイト・プロセッサーごとにv3のプラグイン用ディレクトリを作り、種別を混在させません。
2. 記述子のID・優先順位をmanifest、作者既定値を`plugin.yaml`の`config:`、利用者設定を`plugin_settings.<id>.config`へ移します。秘密情報そのものは外部参照に置き換えます。
3. アプリ・全体・サイト・プロファイルのYAMLをv3のレイヤーへ手動で配置し、プロファイル・サイトのレイヤーから起動設定用のフィールドを除きます。
4. 必要な旧データを確認し、新しいデータルートのプロファイル用ディレクトリへコピーします。旧パスへのフォールバックはありません。
5. ツリーのダイジェスト、manifestのダイジェスト、Ed25519署名を作り直し、strictモードのインストールと`doctor --json`で検証します。
6. プラグインルート・カタログをバックアップし、秘密情報の参照を再解決して、更新状態の移行を確認します。

v2とv3を同じルートで同時に検出することはできません。新しいルートで厳密な検証を完了してから、実行時のプラグインルートを切り替えます。更新状態のスキーマと移行の扱いは[実行時動作リファレンス](../reference/runtime-behavior.md#runtime-update-state)を正本とします。

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

# 旧規則：値を読まずに移行元の名前だけを特定する
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

## QS-015：公開例外名の変更

QS-015では、PEP 8のError接尾辞に統一するため、次の公開名を変更した。
旧名のalias、旧クラス、旧名へのfallbackは提供しない。これは破壊的API変更である。

| 旧名（削除） | 新しい公開名 |
| --- | --- |
| `UnsupportedSiteFeature` | `UnsupportedSiteFeatureError` |
| `SecretNotFound` | `SecretNotFoundError` |

`image_downloader`と`image_downloader.exceptions`の両経路で、importとexceptを新名に変更する。
`ErrorInfo.exception`やログの例外名を比較するコードも新名称へ更新する。
機械的な分類には、変更していない`unsupported_site_feature`・`secret_not_found`のcodeを使える。
`PluginError`／`AuthenticationError`を捕捉するコードは、そのまま同じ例外を捕捉できる。
継承、constructor、固定メッセージ、CLI終了コード（plugin 4／authentication 3）は変更しない。

```python
from image_downloader import SecretNotFoundError, UnsupportedSiteFeatureError
```

外部プラグインが旧名をimport・exceptしている場合はソースを変更し、署名を作り直して
strict検証する。同梱プラグインには旧名の参照がなく、署名済みソースは変更していない。
過去の監査資料や保存済みログの例外名は歴史的記録として残し、自動変換しない。
通常のRuff規約にN818を追加し、同じ命名差分が再発しないようにした。
