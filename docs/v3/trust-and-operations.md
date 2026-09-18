# 信頼・配布・運用

## manifest wrapper

各 plugin unit の `manifest.json` は次の wrapper **だけ**を root に持つ。

```json
{"manifest": {"...": "..."}, "signature": "BASE64_ED25519_SIGNATURE"}
```

inner `manifest` は canonical JSON（key を sort、UTF-8、余分な whitespace なし）へ
serialize した bytes を Ed25519 で署名する。signature は raw 64-byte signature の standard
Base64、`public_key` は raw 32-byte public key の standard Base64 である。`key_id` は raw
public key の lowercase SHA-256 hex である。

inner manifest の field は完全一致で次だけを持つ。未知 field、欠損 field は reject される。

| field | 値 |
|---|---|
| `schema_version` | integer `1` |
| `api_version` | string `"3"` |
| `id` | publisher prefix を持つ reverse-DNS ID。全 plugin で一意 |
| `publisher` | reverse-DNS publisher |
| `version` | PEP 440 version |
| `kind` | `site_plugin` または `image_processor_plugin` |
| `capabilities` | 解釈しない `string[]`。署名対象だが catalog には pin しない |
| `match_priority` | integer。processor では予約 metadata |
| `entry` | `{"file":"relative/path","class":"ClassName"}` |
| `config_file` | relative lowercase `.yaml` author config path |
| `public_key`, `key_id` | signing public key と fingerprint |
| `file_tree` | relative POSIX path から SHA-256 hex への mapping |
| `file_tree_sha256` | canonical `file_tree` JSON の SHA-256 hex |

`id` と `publisher` は lowercase label を dot で連結した form であり、ID は
`publisher + "."` で始まる必要がある。`entry.file` と `config_file` は `file_tree` に
必ず含める。

## file tree

検証対象は plugin directory 配下のすべての regular file である。例外は循環参照を避ける
ための `manifest.json` と `__pycache__/*.pyc` だけである。file tree にない file、hash の
不一致、non-regular file、symbolic link、Windows reparse point は reject される。

- path は plugin directory に対する `/` 区切りの relative path。
- absolute path、`..`、backslash は使えない。
- `__pycache__` 内でも `.pyc` 以外の file は対象である。
- install は cache directory と `.pyc` を copy しない。

作者は author YAML、entry source、relative helper を完成させてから hash を計算し、最後に
manifest を署名する。署名後に source/YAML を編集すると trust/strict verification は失敗
する。project は signing private key を同梱・自動生成・配布しない。作者が明示的に
`tools/sign_local_site_plugin.py --create-key` を指定した場合だけ、その指定先に鍵を生成する。

## catalog

catalog path は設定では変更できず、常に `<plugin-root>/catalog.json`。root schema は
次であり、root/entry とも unknown field を許可しない。

```json
{
  "schema_version": 1,
  "plugins": [
    {
      "id": "com.example.gallery",
      "kind": "site_plugin",
      "publisher": "com.example",
      "version": "1.0.0",
      "public_key": "...",
      "key_id": "...",
      "manifest_digest": "...",
      "file_tree_sha256": "...",
      "selection_priority": 0,
      "revoked": false
    }
  ]
}
```

catalog は ID、kind、publisher、version、public key/fingerprint、manifest digest、tree
digest、selection priority、revocation を pin する。`capabilities` は catalog に書かない。
catalog は原子的に replace され、最初の trust が存在しない catalog を作成する。
install/trust/revoke の read-modify-write 全体は `<plugin-root>/.catalog.lock` のprocess間lockで
直列化される。配置またはcatalog commitに失敗したinstallは、追加・置換したdirectoryをrollbackする。
installとtrustは同じ署名・file tree検証とcatalog遷移policyを使用する。したがって、同じversionの
内容変更、publisher/kind変更、署名またはtree不一致は、どちらの操作でも同じ基準で拒否される。

## verification mode

`security.plugin_verification` は main app / profile app layer にだけ書ける。

| mode | catalog がない | catalog が壊れている | individual plugin verification failure |
|---|---|---|---|
| `strict` | plugin directory がなければ external registry は空。あれば各 plugin を failed/skip | configuration error | failed diagnostic、skip |
| `warn` | structural に有効な plugin を warning 付きで利用 | warning 付きで catalog を無視 | warning 付きで unpinned load を試行 |
| `off` | catalog を見ない | catalog を見ない | manifest/path/tree の structural check は継続 |

catalog の active entry に対応する installed directory がない場合は stale warning であり、
他 plugin の execution を止めない。plugin root 不在かつ catalog 不在は正常な空 registry
である。builtin generic fallback は別に利用できる。

`security.plugin_verification: off` は `--allow-unverified-plugins` を同じ invocation に
明示した時だけ使える。これは catalog pin を外すが、manifest/path/tree schema は緩めない。

## 管理コマンド

すべての source path と `--plugin-root` は absolute path を用いる。

```powershell
# staged copy → signature/tree/class contract → atomic placement + catalog trust
image-downloader plugin install "C:\build\gallery-plugin" `
  --plugin-root "C:\ProgramData\image-downloader\plugins" --yes

# catalog だけに trust entry を作成/更新する
image-downloader plugin trust "C:\build\gallery-plugin" `
  --plugin-root "C:\ProgramData\image-downloader\plugins" --selection-priority 10

image-downloader plugin list --plugin-root "C:\ProgramData\image-downloader\plugins" --json
image-downloader plugin revoke com.example.gallery --plugin-root "C:\ProgramData\image-downloader\plugins"
```

install/trust/revoke は confirmation を要求する。non-interactive automation では
`--yes` を付ける。install/trust 前には ID、publisher、kind、version、key fingerprint、tree
digest を表示する。`--selection-priority` の既定は `0`。

同一 ID を更新する時の制約は次のとおり。

- kind または publisher の変更は拒否。
- 同一 version で manifest/tree content が変わる更新は拒否。
- source directory名が変わっていてもmanifest IDが一致する既存directoryを更新し、同一IDを
  別directoryへ重複installしない。
- version を進めた key rotation、version downgrade、revoked ID の trust/install、priority
  変更は confirmation 後に許可。
- `revoke` は catalog entry を削除せず `revoked: true` にする。再 trust/install は
  confirmation 後に解除できる。
- uninstall command はない。directory を手動削除する前に catalog/revocation の運用方針を
  決めること。

## discovery と運用診断

discovery は parent directory の child を読み、valid manifest ID 昇順で処理する。同一 ID
は compose configuration error。plugin root の `catalog.json`、`site_plugins`、
`image_processor_plugins` 以外の entry は runtime では無視し、`doctor`/`plugin list` では
warning になる。

`doctor`/`doctor --json` は従来の`loaded`、`warning`、`detail`、検証 mode、profile data pathに
加え、application/library version・root、実際のplugin root、伏字化した有効設定とruntime
override、受理済みpluginのpublisher/version/source/manifest fingerprint/catalog trust/configを
表示する。受理前に失敗したpluginは`plugins[]` diagnostic、受理済みpluginのメタデータは
`loaded_plugins[]`で確認する。warningだけなら exit 0。failed diagnostic が一件でも
unhealthy/exit 4 である。
特に署名、tree hash、link/reparse point、class contract、disabled processor、stale catalog
entry を release 前に確認する。
