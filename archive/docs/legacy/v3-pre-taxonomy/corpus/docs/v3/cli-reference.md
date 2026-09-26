# CLI 参照

<a id="cli-canonical-forms"></a>

この文書は CLI の command line、適用可能な option、終了状態、標準出力の正本である。設定値そのものは [設定参照](configuration-reference.md)、plugin package は [plugin 開発参照](plugin-development-reference.md) を参照する。すべての成功 JSON は標準出力にちょうど一つの JSON object として出る。`--json` を指定した download では chapter log を標準出力へ出さない。診断用の人間向けエラーは標準エラーに出る。

## Canonical forms

<a id="cli-forms"></a>

```text
image-downloader download URL [options]
image-downloader URL [download options]             # bare-URL compatibility form
image-downloader doctor [--host HOST] [options]
image-downloader config path|explain|init [arguments] [options]
image-downloader config profile init NAME [options]
image-downloader plugin list|install|trust|revoke|uninstall [arguments] [options]
image-downloader cookie export|import|browser-import VALUE [options]
```

bare URL は parser が `download` を補う旧 form であり、command としては `download` が正本である。旧 `--export-cookies PATH`、`--import-cookies PATH`、`--import-browser-cookies DOMAIN` は互換 form として cookie operation を選ぶ。一 invocation で複数の旧 cookie form は選べない。

## Options

<a id="cli-options"></a>

| option | value / default | command | effect |
| --- | --- | --- | --- |
| `--config` | absolute `Path` / automatic user config | download, doctor, config explain/profile init, plugin, cookie | main config を選ぶ。`config path` と `config init` では不可。 |
| `--profile` | name / config の `profile.default` | download, doctor, config explain, plugin, cookie | profile layer を選ぶ。 |
| `--data-root` | absolute path / none | download, doctor, config explain/init, plugin, cookie | この実行だけ storage root を override。 |
| `--plugin-root` | absolute path / none | download, doctor, config explain/init, plugin, cookie | この実行だけ plugin/catalog root を override。 |
| `--yes` | flag / false | download, doctor, config init/profile init, plugin, cookie | state-changing setup/mutation の明示承認。 |
| `--plugin-verification-override` | `bypass-all` \| `bypass-catalog` \| `bypass-signature` | download, doctor, config explain, plugin, cookie | 一回だけ verification policy を弱める管理者用 override。保存しない。 |
| `--json` | flag / false | 全 command | 成功を schema 化し、失敗を [error object](#cli-errors) にする。 |
| `--plugin-config` | repeatable `ID=JSON` / `[]` | download, doctor | 選択される site plugin または有効 processor への一回限り mapping override。 |
| `--plugin-config-file` | repeatable absolute path / `[]` | download, doctor | 上と同じ override を YAML/JSON file から読む。 |
| `--fallback-generic` | `auto` \| `enabled` \| `disabled` / `auto` | download, doctor | builtin generic HTML fallback を config から使うか一回だけ切替える。 |
| `--no-console-log` | flag / false | download, doctor, config explain | console chapter log を無効化する runtime override。 |
| `--list-updated-urls` | flag / false | download | download の代わりに selected `UpdateProvider` の snapshot を比較する。 |
| `--existing-file` | `overwrite` \| `skip` \| `rename` \| `error` | download, doctor, config explain | output collision policy の一回限り override。 |
| `--image-format` | `JPEG` \| `PNG` \| `WEBP` | download, doctor, config explain | output format の一回限り override。 |
| `--host` | bare host または absolute HTTP(S) URL | doctor, config explain | site overlay と plugin selection を network を使わず検査する。 |
| `--selection-priority` | integer / `0` | plugin install/trust | catalog selection priority。 |

parser は表にない command/option の組合せを configuration failure として拒否する。`config init [ABSOLUTE_PATH]` は明示 path がないと固定 user config を作る。`config profile init NAME` の NAME は `[A-Za-z0-9_-]+` で、必要なら main config を先に作る。

## Exit status

<a id="cli-exit-status"></a>

| code | constant | meaning |
| --- | --- | --- |
| 0 | `EXIT_SUCCESS` | operation succeeded |
| 1 | `EXIT_FAILURE` | general failure、または全画像が失敗 |
| 2 | `EXIT_CONFIGURATION` | argument/configuration validation failure |
| 3 | `EXIT_AUTHENTICATION` | authentication or secret failure |
| 4 | `EXIT_PLUGIN` | discovery, verification, or plugin execution failure |
| 5 | `EXIT_PARTIAL` | some image saved/skipped and at least one image failed |
| 130 | — | `KeyboardInterrupt`; payload は出力しない |

## Success JSON

<a id="cli-success-json"></a>

未知の追加 field は将来追加され得るため consumer は許容する。path は文字列で、download の `saved`/`skipped` は absolute path、管理 command が返す source/path は command 入力または設定 root を表す。

| command | success object |
| --- | --- |
| `download URL --json` | `{"saved": string[], "skipped": string[], "failures": ImageFailureJson[]}` |
| `download URL --list-updated-urls --json` | `{"updated_urls": string[], "removed": integer}` |
| `doctor --json` | `{"healthy": boolean, "application": object, "library": object, "plugin_root": string, "verification": string, "configuration": object, "paths": object, "loaded_plugins": object[], "plugins": object[]}` |
| `config path --json` | `{"user_config": string, "user_config_exists": boolean, "package_baseline": string, "default_data_root": string, "default_plugin_root": string}` |
| `config explain --json` | `{"source": string, "main_config_kind": string, "main_config": string|null, "config_root": string, "selected_profile": string, "target_host": string|null, "layers": LayerJson[], "effective": object, "origins": object, "runtime_overrides": object}` |
| `config init … --json` | `{"created_config": string}` |
| `config profile init NAME --json` | `{"main_config": string, "created_main_config": boolean, "profile_config": string}` |
| `cookie ACTION VALUE --json` | `{"operation": "cookie", "action": "export"|"import"|"browser-import", "target": string}` |
| `plugin list --json` | `{"plugin_root": string, "plugins": PluginListJson[], "diagnostics": object[], "catalog_warning": string|null}` |
| `plugin install` / `trust` / `revoke` `--json` | `CatalogEntryJson` |
| `plugin uninstall --json` | `{"plugin_id": string, "removed_installation": boolean, "removed_catalog": boolean}` |

`ImageFailureJson` has `kind` (`fetch|process|save`), `exception`, `message`, `code`, `reason`, `output_path`, `response_url`, `http_status`, and `transport`. A field may be `null` where that per-image failure did not produce the relevant information. `LayerJson` has `role`, `path` (`string|null`), and `status`. `effective` and `runtime_overrides` redact protected headers, proxy credentials, and secrets.

`CatalogEntryJson` has one of two shapes. A normal pin has `id`, `kind`, `publisher`, `version`, `public_key`, `key_id`, `manifest_digest`, `file_tree_sha256`, `selection_priority`, `revoked`. A content pin has only `id`, `kind`, `manifest_digest`, `content_digest`, `selection_priority`, `revoked`; its omitted publisher/key fields are not `null` placeholders. `PluginListJson` is the same catalog information with discovery/loading status; treat it as diagnostic data rather than a manifest replacement.

## Errors and redaction

<a id="cli-errors"></a>

Every handled `--json` failure uses this wrapper:

```json
{
  "error": {
    "operation": "download|doctor|config|plugin|cookie",
    "exception": "safe exception class name",
    "code": "stable_reason_code",
    "reason": "stable safe reason",
    "message": "safe message"
  }
}
```

The five fields above are required. `response_url`, `http_status`, `output_path`, `stage`, and `transport` appear **only** when the error supplies them; absence is not the same as `null`. URL query/fragment secrets are masked, unsafe exception names are normalized, and cookie contents, passphrases, browser records, and raw credential references never appear in a success or error payload. Human-readable errors use the same safe message but are written to standard error.

## State-changing commands

<a id="cli-state-effects"></a>

`download`, update listing, cookie operations, and plugin mutations can create an initial fixed user config when none exists. Its snapshot freezes then-current bundled defaults; CLI overrides are not persisted. Existing user config can be rewritten to remove obsolete/unknown static keys before those state-changing operations. `doctor`, `config path`, `config explain`, and `plugin list` are read-only. `plugin install` stages and validates a copy, then atomically places/trusts it; `revoke` retains the catalog record with `revoked: true`; `uninstall` removes installation and catalog entry. Back up the plugin root before administrator mutations.
