# v2 plugin package・署名ガイド（履歴資料）

> v3 は wheel sidecar ではなく signed directory manifest を使います。現行の manifest/catalog/CLI は [v3 信頼・配布・運用](../../../../docs/v3/trust-and-operations.md) を参照してください。

この文書は、plugin作者が0.3.0対応wheelへsidecarを組み込み、管理者がcatalogへpinするための仕様です。coreはsidecar生成、署名、wheelの`RECORD`更新を行いません。

## 1. package metadataとentry point

```toml
[project]
name = "example-site-plugin"
version = "1.0.0"
requires-python = ">=3.11"
dependencies = ["image-downloader>=0.3,<0.4"]

[project.entry-points."image_downloader.plugins"]
example-site = "example_site.plugin:ExampleSitePlugin"
```

画像processorの場合はgroupを変えます。同じdistributionに両方を含めることもできます。

```toml
[project.entry-points."image_downloader.image_processors"]
watermark = "example_site.processor:WatermarkProcessor"
```

entry point値は引数なしclassです。v1 group、instance、factoryは受理されません。

## 2. sidecarの配置とtop-level schema

最終wheelの`<distribution>-<version>.dist-info/`直下へ、次の名前で1ファイルだけ置きます。

```text
image_downloader_plugin.json
```

top-level objectは`manifest`と`signature`を持ちます。

```json
{
  "manifest": {
    "schema_version": 1,
    "id": "com.example.gallery",
    "distribution": "example-site-plugin",
    "publisher": "Example Publisher",
    "api_version": "2",
    "key_id": "0123456789abcdef...64 lowercase hex characters...",
    "capabilities": ["site-downloader"],
    "file_tree": {
      "example_site/__init__.py": "sha256-hex",
      "example_site/plugin.py": "sha256-hex",
      "example_site_plugin-1.0.0.dist-info/METADATA": "sha256-hex",
      "example_site_plugin-1.0.0.dist-info/WHEEL": "sha256-hex",
      "example_site_plugin-1.0.0.dist-info/entry_points.txt": "sha256-hex"
    },
    "file_tree_sha256": "sha256-of-canonical-file-tree"
  },
  "signature": "base64-ed25519-signature"
}
```

manifestは次の9 fieldを過不足なく持つ必要があります。

| field | 型 | 仕様 |
|---|---|---|
| `schema_version` | integer | `1` |
| `id` | string | `PluginDescriptor.id`およびcatalog IDと完全一致 |
| `distribution` | string | installed distributionの`Name`。`_`と`-`、大文字小文字は照合時に正規化 |
| `publisher` | string | catalogのpublisherと完全一致 |
| `api_version` | string | `"2"` |
| `key_id` | string | raw Ed25519 public key bytesのSHA-256 lowercase hex |
| `capabilities` | string array | `site-downloader`、`image-processor`の必要な方だけ |
| `file_tree` | object | POSIX相対pathからfile bytesのSHA-256 lowercase hexへの完全map |
| `file_tree_sha256` | string | canonical file_tree bytesのSHA-256 lowercase hex |

未知capabilityはsidecarの構造エラーとして拒否されます。

## 3. canonical JSON

署名対象はtop-level objectではなく`manifest` objectだけです。manifestをRFC 8785 JCSでcanonicalizeしたUTF-8 bytesをEd25519で署名します。

0.3.0のmanifest schemaには浮動小数点を使用しません。coreが期待する表現は、UTF-8、Unicode非ASCII文字をescapeせず、object keyを辞書順、separatorを`,`と`:`にしたJSONです。概念的には次です。

```python
json.dumps(
    manifest,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
    allow_nan=False,
).encode("utf-8")
```

署名toolはRFC 8785準拠実装を使い、上の式とgolden fixtureが一致することを確認してください。

## 4. file tree

installed distributionが所有する全fileを対象にし、次の2種類だけを除外します。

- sidecar自身（basenameが`image_downloader_plugin.json`）
- wheelの`RECORD`（basenameが`RECORD`）

directoryは含めずregular fileだけを含めます。keyはwheel内のPOSIX path、valueはfile bytesのSHA-256 hexです。mapはkey順に並べたcanonical JSONへ変換し、そのbytesのSHA-256を`file_tree_sha256`にします。

`METADATA`、`WHEEL`、`entry_points.txt`、licensesなど、sidecar/RECORD以外の`.dist-info` fileも省略できません。インストール後に生成される`.pyc`やcacheはdistribution所有fileではないため含めません。

## 5. keyと署名

1. Ed25519 private/public key pairを安全な署名環境で生成する。
2. raw形式の32-byte public keyをSHA-256化し、hex digestを`key_id`にする。
3. private keyでcanonical manifest bytesを署名する。
4. 64-byte署名をstandard Base64でencodeし、top-level `signature`へ入れる。
5. raw public keyもstandard Base64でencodeし、管理者catalogへ渡す。

private keyをwheel、repository fixture、catalog、sidecarへ含めてはいけません。coreはcatalogのBase64 public keyをdecodeし、そのSHA-256がcatalogとmanifestの`key_id`に一致することを検証します。

## 6. wheel作成順序

署名後にfile tree対象fileを変更すると検証に失敗します。次の順序を固定してください。

1. 通常のwheelをbuildする。
2. 一時directoryへwheelを展開する。
3. sidecarと`RECORD`以外の全配布fileから`file_tree`を作る。
4. `file_tree_sha256`を計算してmanifestを完成する。
5. canonical manifestを署名し、sidecarを書き込む。
6. wheel仕様に従い`RECORD`へsidecarのhash/size entryを追加し、`RECORD`自身のentryを更新する。
7. wheelを再packする。
8. clean environmentへinstallし、installed distributionを対象に同じtreeを再計算する。
9. strict catalogで`image-downloader doctor --json`を実行する。

sidecarを書いた後に`METADATA`やentry pointを編集してはいけません。編集した場合はtree計算と署名からやり直します。

## 7. catalog schema

catalogは次のtop-level schemaです。

```json
{
  "schema_version": 1,
  "plugins": [
    {
      "id": "com.example.gallery",
      "distribution": "example-site-plugin",
      "publisher": "Example Publisher",
      "key_id": "sha256-of-raw-public-key",
      "public_key": "base64-raw-ed25519-public-key",
      "capabilities": ["site-downloader"],
      "manifest_digest": "sha256-of-canonical-manifest",
      "file_tree_sha256": "sha256-of-canonical-file-tree",
      "selection_priority": 100,
      "revoked": false
    }
  ]
}
```

| field | 検証・用途 |
|---|---|
| `id`, `distribution`, `key_id` | manifestを検索する複合identity |
| `publisher` | manifestと完全一致 |
| `public_key` | Base64 raw Ed25519 public key |
| `capabilities` | distributionに許可する能力の上限。manifest capabilitiesはこのsubsetであること |
| `manifest_digest` | canonical manifest bytesのSHA-256 |
| `file_tree_sha256` | manifestのtree digestと完全一致 |
| `selection_priority` | 同じ`PluginDescriptor.priority`間の第2選択key。JSON integer必須 |
| `revoked` | trueならpluginを拒否。省略時false |

同一distributionにsite pluginとprocessorを含む場合、manifest/catalog capabilitiesへ両方を含めます。同じsidecarが各entry pointの必要capabilityについて検証されます。

## 8. catalog path安全性

`security.plugin_catalog`はメイン設定fileの親directoryを基準に解決します。次を拒否します。

- 絶対pathまたはdrive指定path
- path component `..`
- 基準directory外へ解決されるpath
- 欠落file（strictの場合）
- directory、symlink、Windows reparse pointなどregular file以外

```yaml
security:
  plugin_catalog: plugins.catalog.json
  plugin_verification: strict
```

## 9. verification mode

| mode | catalog/暗号検証 | sidecar構造確認 | import |
|---|---|---|---|
| `strict` | 必須 | 必須 | 検証成功後だけ |
| `warn` | catalogが使えれば検証。不備はwarning | 必須 | 構造的v2 pluginはwarning付きで可能 |
| `off` | 省略 | 必須 | 構造的v2 pluginだけ可能 |

どのmodeでも、sidecar欠落、sidecar複数、必須field過不足、api version不正、未知capabilityはentry point import前に拒否します。offはv1互換modeではありません。

strictのcatalog自体が欠落・不正ならcomposeが`ConfigurationError`になります。個別pluginの署名、pin、revocation、tree不一致はそのpluginをfailed diagnosticとしてskipします。`doctor --json`の`plugins[]`で`loaded`、`warning`、`detail`を確認してください。

## 10. release verification

clean environmentで少なくとも次を確認します。

```powershell
python -m pip install image-downloader==0.3.0
python -m pip install .\dist\example_site_plugin-1.0.0-py3-none-any.whl
image-downloader doctor --config .\app.yaml --json
```

さらに、署名、key ID、publisher、capability、manifest digest、tree digest、revocationを一つずつ改変したnegative fixtureを用意し、すべてimport前にfailed diagnosticになることを検証してください。
