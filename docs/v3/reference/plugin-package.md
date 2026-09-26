# Plugin package and trust reference

<a id="plugin-package"></a>

site unit は `<plugin-root>/site_plugins/<unit>/`、processor unit は `<plugin-root>/image_processor_plugins/<unit>/` に置く。runtime が読む必須 source は `manifest.json`、manifest の `config_file` が指す author-default YAML、entry module と relative helper である。author-default の filename は固定ではない（template の `sample_plugin.yaml` も有効）。その YAML は `config` だけを root key に持つ。package の schema と署名はこの文書だけで定義し、hook 実装は [plugin hook reference](plugin-hooks.md) を参照する。

<a id="plugin-layout"></a>

<a id="plugin-signer-metadata"></a>

`plugin-metadata.json` は bundled signer の任意入力であり runtime は読まない。別の方法で complete manifest を生成・署名する author は置く必要がない。bundled `tools/sign_local_site_plugin.py` は metadata の `kind` に従い site plugin と image processor plugin の両方を署名できる。signer が読む metadata は unknown/missing field を許さない exact object である。

| exact field | value |
| --- | --- |
| `id` | reverse-DNS plugin ID |
| `publisher` | lowercase publisher ID |
| `version` | PEP 440 version |
| `kind` | `site_plugin` または `image_processor_plugin` |
| `capabilities` | informational string list。実行権限を付与しない。 |
| `match_priority` | site selection に使う integer。processor では metadata として保持される。 |
| `entry` | `file` と `class` を持つ object |
| `config_file` | relative `.yaml` author-default file |

<a id="plugin-manifest"></a>

## Manifest and file tree

strict `manifest.json` wrapper は exact keys `manifest` と `signature` を持つ。inner manifest は unknown/missing field を許さない exact 14-field object である。

| strict inner field | validation / meaning |
| --- | --- |
| `schema_version` | integer `1` |
| `id` | reverse-DNS plugin ID |
| `publisher` | lowercase publisher ID |
| `version` | PEP 440 version |
| `api_version` | string `"3"` |
| `kind` | `site_plugin` または `image_processor_plugin` |
| `capabilities` | string list。情報表示用であり capability grant ではない。 |
| `match_priority` | integer。site candidate の selection priority。 |
| `entry` | exact `{ "file": relative entry file, "class": non-empty class name }` |
| `config_file` | relative `.yaml` file。author-default YAML を指す（名前は固定ではない）。 |
| `public_key` | base64 Ed25519 public key |
| `key_id` | key identifier |
| `file_tree` | source file path から SHA-256 への mapping |
| `file_tree_sha256` | canonical file-tree digest |

`signature` は inner manifest の Ed25519 signature である。`config` は strict inner manifest の field ではない。author default は `config_file` が指す YAML の root `config` にだけ置く。

### Relaxed manifest used by bypass modes

`off`（実効 mode は `bypass-all`）、`bypass-all`、`bypass-signature` が読む source は relaxed manifest を許す。これは strict schema ではない。wrapper の `signature` は省略でき、inner manifest で必須なのは `id`、`kind`、`entry` だけである。`match_priority` がなければ runtime は `0` を補完する。`config_file` は任意だが、あれば relative `.yaml` として検証する。`bypass-catalog` は relaxed manifest ではなく complete signed/tree-verified manifest を要求する。relaxed mode は strict source の 14-field schema を変更せず、production の trust policy に使ってはならない。

`file_tree` は `manifest.json` を除く全 regular source file の POSIX relative path から lowercase SHA-256 hex への mapping である。link、Windows reparse point、hard link、non-regular file は許可されない。`__pycache__/*.pyc` だけは除外される。helper、metadata、author YAML を変更したら tree/hash/signature を再生成する。

## Catalog and verification

<a id="plugin-catalog"></a>

catalog path は常に `<plugin-root>/catalog.json`、root schema version は `1` であり unknown root/entry field は許可しない。normal pin は ID、kind、publisher、version、public key、key ID、manifest digest、file-tree digest、selection priority、revoked を持つ。content pin は `id`、`kind`、`manifest_digest`、`content_digest`、`selection_priority`、`revoked` だけを持ち、publisher/key fields は null placeholder ではない。

`<plugin-root>/.catalog.lock` は install/trust/revoke/uninstall の read-modify-write を process 間で直列化する。catalog は atomic replace され、install の placement/catalog commit が失敗したら directory を rollback する。stale catalog entry は warning であり、他 plugin の実行を止めない。plugin root と catalog がともにない状態は空 registry として正常である。

| mode | behavior |
| --- | --- |
| `strict` | catalog と complete manifest/tree/signature が必要。failure unit は failed diagnostic として skip。 |
| `warn` | structural validation 後に catalog/verification failure を warning として unpinned load を試みる。 |
| `off` / `bypass-all` | catalog、signature、tree を迂回し minimum manifest を許す。 |
| `bypass-catalog` | catalog だけを迂回し complete source signature/tree は必須。 |
| `bypass-signature` | content-pinned catalog と actual content digest を必須にし signature だけを迂回。 |

production は `strict` を使う。weak mode は一回限りの復旧/開発であり persisted policy を置換しない。

<a id="plugin-signing"></a>

## Management policy

install は staged copy、signature/tree/class validation、atomic placement、catalog trust を行う。trust は catalog entry を作成/更新、revoke は entry を残して `revoked: true`、uninstall は installation と entry を transactional に削除する。source directory 名ではなく manifest ID が identity である。

kind/publisher 変更と同 version の content/tree 変更は拒否する。version を進めた key rotation、downgrade、revoked ID の再 trust、selection priority 変更は confirmation 後に許可する。install/trust/revoke/uninstall は confirmation を要求し、non-interactive automation では `--yes` を使う。private signing key を repository/fixture/package に同梱・自動生成してはならず、明示的な signer `--create-key` だけが指定先に生成できる。

<a id="plugin-signing-workflow"></a>

### Reproducible bundled-signer workflow

The bundled signer is a convenience for a locally controlled key; it is not a
runtime dependency and `plugin-metadata.json` is not a second runtime manifest.
Starting with a unit that contains its entry module, helpers, author-default
YAML, and an exact eight-field `plugin-metadata.json`:

```powershell
# Create the private Ed25519 key once at a protected user-controlled path.
python tools/sign_local_site_plugin.py --key C:\secure\image-downloader-plugin.pem --create-key .\my-plugin

# On later source/default/helper changes, regenerate file_tree, digests, and signature.
python tools/sign_local_site_plugin.py --key C:\secure\image-downloader-plugin.pem .\my-plugin

# Validate the generated manifest while copying/pinning the unit, then inspect it.
image-downloader plugin install .\my-plugin --yes
image-downloader doctor --json
```

The signer reads only `plugin-metadata.json`, generates `manifest.json`, and
includes all regular source files other than `manifest.json` in `file_tree`.
Do not manually edit the generated manifest after signing.  Change metadata,
the author-default YAML, or source first, then re-run the signer. `plugin
install` performs strict source/tree/signature/class validation by default and
creates the matching catalog pin. Use `image-downloader plugin trust SOURCE
--yes` only to pin an already placed source. The template explains the manual
equivalent and its `sample_plugin.yaml` mapping; source examples show signed
site and processor units.
