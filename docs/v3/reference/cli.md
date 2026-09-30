# CLI reference

<a id="cli-forms"></a>

Canonical command forms are:

```text
image-downloader download URL [--force-image-format FORMAT] [options]
image-downloader inspect URL [options]
image-downloader download URL --inspect-only [options] # inspect の alias
image-downloader URL [download options]                 # bare-URL compatibility form
image-downloader doctor [--host HOST_OR_URL] [options]
image-downloader config path|explain|init [arguments] [options]
image-downloader config profile init NAME [options]
image-downloader plugin list|install|trust|revoke|uninstall [arguments] [options]
image-downloader cookie export|import|browser-import VALUE [options]
```

legacy `--export-cookies`、`--import-cookies`、`--import-browser-cookies` select one cookie action and cannot be combined. `URL` bare form is rewritten to `download`.

<a id="cli-state-effects"></a>

config 作成・rewrite の command ごとの副作用は [configuration reference](configuration.md#config-layers) が正本である。`config path` と `config explain` は観測専用である。

## Option acceptance and effect

<a id="cli-options"></a>

`--json` is accepted by every command. The parser also carries some global fields to every handler; **accepted** does not imply an effect. A handler rejects a listed incompatible option with `ConfigurationError`.

| option | accepted and effective commands | accepted/no effect or rejected behavior |
| --- | --- | --- |
| `--config PATH` | download, inspect, doctor, config explain/profile init, plugin, cookie | rejected by config path/init |
| `--profile NAME` | download, inspect, doctor, config explain, plugin, cookie | rejected by config path/init/profile init |
| `--data-root PATH`, `--plugin-root PATH` | download, inspect, doctor, config explain/init, plugin, cookie | rejected by config path and profile init |
| `--yes` | plugin install/trust/revoke/uninstall confirmation | config init/profile init accept it but do not require or use confirmation; other parsed uses do not approve a mutation |
| `--plugin-verification-override MODE` | download, inspect, doctor, plugin | config commands may report the parsed value but do not perform verification; cookie has no verification effect |
| `--plugin-config ID=JSON`, `--plugin-config-file PATH`, `--fallback-generic` | download, inspect, doctor | rejected by config, plugin, cookie |
| `--plugin ID`, `--force-plugin ID` | download（`--list-updated-urls` を含む）, inspect | rejected by doctor, config, plugin, cookie |
| `--plugin-download-policy ID=JSON`, `--plugin-download-policy-file PATH` | download（`--list-updated-urls` を含む） | inspection を含む他 command では rejected |
| `--no-console-log`, `--existing-file`, `--image-format` | download, doctor, config explain where handler permits them | inspection、cookie、other config/plugin operations では rejected |
| `--force-image-format FORMAT` | normal download and bare-URL download | `--image-format` と排他的。inspect、doctor、config、plugin、cookie、update listing では rejected |
| `--list-updated-urls` | download | rejected elsewhere |
| `--inspect-only` | `download URL` の inspection alias | `--list-updated-urls` と排他的 |
| `--manifest-only` | inspect, `download URL --inspect-only` | normal download では rejected。`create_image_request()` を呼ばない |
| `--inspection-data url\|http\|all` | inspect, `download URL --inspect-only` | stdout の inspection data level。既定は `url`。normal download では rejected |
| `--host` | doctor, config explain | rejected elsewhere |
| `--selection-priority INT` | plugin install/trust | rejected elsewhere |

`--plugin-config` is repeatable `ID=<JSON-object>`. `--plugin-config-file` is repeatable, absolute, existing regular non-link JSON file whose exact object is `{"plugin_id": "…", "config": {…}}`. File mappings are deep-merged in command-line file order; inline `--plugin-config` mappings are then deep-merged in their appearance order. YAML is not accepted for this option.

`--plugin-download-policy` is repeatable `ID=<JSON-object>`. `--plugin-download-policy-file` is repeatable, absolute, existing regular non-link JSON file whose exact object is `{"plugin_id": "…", "download_policy": {…}}`. These mappings are field-wise merged for the selected site plugin only, are validated against the core-owned `download_policy` schema, and are never persisted. `request_concurrency`, `chapter_concurrency`, `image_concurrency_per_chapter`, and `preserve_image_start_order` are defined in [configuration](configuration.md#config-plugin-settings).

`--plugin-verification-override` accepts `bypass-all`, `bypass-catalog`, or `bypass-signature`. It is an ephemeral override, not a persisted `security.plugin_verification` value; the persisted choices are `strict`, `warn`, and `off`. `plugin list` **does apply** the override while it discovers and verifies source units for `diagnostics`; it does not alter the catalog-entry `plugins` list or write persistent configuration/catalog state.

`--plugin ID` requires that enabled non-builtin site plugin's normal matcher to accept the URL. `--force-plugin ID` bypasses that matcher only after the plugin is confirmed to be an enabled non-builtin site plugin; the two options are exclusive. Neither can be combined with an explicit `--fallback-generic=enabled|disabled`. `--fallback-generic` accepts `auto` (the parser default), `enabled`, or `disabled`; `auto` preserves the resolved configuration. `--existing-file` accepts `overwrite`, `skip`, `rename`, or `error`.

`--image-format` accepts `ORIGINAL`, `JPEG`, `PNG`, or `WEBP` and is a one-operation override of resolved `output.image_format`; an image whose plugin specifies `ImageResource.save_options.format` keeps the plugin format. `--force-image-format` has the same values but is mutually exclusive with `--image-format` and wins over the plugin. `--force-image-format ORIGINAL` preserves the final plugin-produced artifact bytes without core re-encoding; it is not an HTTP-response raw-download mode. A forced encoded format clears a plugin-provided filename extension so the output suffix matches the forced bytes. Output format tokens use explicit `%CONTENT_TITLE%`, `%CHAPTER_TITLE%`, `%CHAPTER_SUBTITLE%`, `%CHAPTER_NUMBER%`, and `%IMAGE_INDEX%`; `%NUM%`, `%TITLE%`, and `%SUBTITLE%` are rejected. See [configuration](configuration.md#config-storage-layout).

<a id="cli-exit-status"></a>

| code | meaning |
| --- | --- |
| 0 | success |
| 1 | general failure or all images failed |
| 2 | argument/configuration validation failure |
| 3 | authentication or secret failure |
| 4 | plugin discovery, verification, or execution failure |
| 5 | partial image result、または inspect の一部 image request 解決失敗 |
| 130 | `KeyboardInterrupt`; no JSON payload |

<a id="cli-io"></a>

## Command I/O and side effects

This table is intentionally about the command handler, rather than merely what
`argparse` can parse.  “Read” means an input that can affect the result;
“write” means a persistent filesystem effect.  `--json` changes only the
successful stdout representation, never the inputs or the write set.

| command | positional input | read | write / operation | stdout and stderr | exit |
| --- | --- | --- | --- | --- | --- |
| `download URL` | one absolute HTTP(S) URL | resolved config/layers, selected plugin source/catalog, cookie jar, update state (only for listing) | initial user config may be created/re-written; normal mode writes downloads, reports, logs, cookie delta; listing writes update state and normal runtime state but **does not download images** | normal: chapter/result output; JSON: one result object; diagnostics and JSON-mode plugin `print()` go to stderr | 0, 1, 3, 4, or 5 |
| `download URL --list-updated-urls` | same URL | selected site plugin and its `UpdateProvider`, update state | same bootstrap/rewrite behavior; update-state snapshot/lock; no image/output allocation | URL per added/changed candidate on stdout, summary on stderr; JSON has `updated_urls` and `removed` | 0 or operation failure |
| `inspect URL` / `download URL --inspect-only` | one absolute HTTP(S) URL | resolved config/layers, selected plugin source/catalog, profile cookie snapshot | neither creates/re-writes config nor persists cookie changes, debug logs, update state, output, reports, events, or notifications; it runs site `inspect()` and, unless `--manifest-only`, serially runs every `create_image_request()` and `AuthFlow.apply()` to build a no-send effective request. It never fetches an image body, recovers, transforms, saves, or allocates output. Plugin helper HTTP may still have server-side effects. | exactly one inspection JSON object on stdout (pretty JSON without `--json`); plugin `print()` goes to stderr. `--inspection-data url` is the default safe-minimum projection; `http` / `all` can contain URL query, headers, cookies, bodies, tokens, and `plugin_data`. Never forward either stream to logs, CI artifacts, telemetry, tickets, or third parties. | 0, 5 if one or more resolutions fail, or 2/3/4/1 operation failure |
| `doctor [--host HOST_OR_URL]` | no positional input; host is a bare host or absolute HTTP(S) URL | resolved config/layers, plugin source/catalog, optional selection candidates | none: it uses registry-only composition and does not create/rewrite config, catalog, cookie, or download files | human report or exactly one JSON object | 0 when healthy; 4 for unhealthy plugin diagnostics; 2/4 for handled error |
| `config path` | none | platform paths and package baseline locations | none | paths object or human paths | 0 / 2 |
| `config explain [--host HOST_OR_URL]` | none | selected configuration layers and runtime options | none | effective/origin report | 0 / 2 |
| `config init [ABSOLUTE_PATH]` | zero or one absolute main-config path | template and destination safety metadata | creates exactly that missing main YAML; never overwrites | created path object / message | 0 / 2 |
| `config profile init NAME` | one profile name | main config presence and path safety | creates missing main config, then one missing profile YAML | main/profile paths object / message | 0 / 2 |
| `plugin list` | none | resolved config, catalog and discovered source units | none; verification override affects only discovery diagnostics | catalog entries and diagnostics | 0 / 2 / 4 |
| `plugin install SOURCE`, `trust SOURCE`, `revoke ID`, `uninstall ID` | source directory or manifest ID as appropriate | resolved config, source/catalog and confirmation input | may bootstrap/rewrite config; transactional plugin directory/catalog mutation | catalog entry or uninstall object | 0 / 2 / 4 |
| `cookie export PATH` | destination path | resolved config and profile cookie store | encrypted export file | action/target object or message | 0 / 2 / 3 |
| `cookie import PATH` | existing encrypted export path | resolved config, import file, profile cookie store | merges/saves encrypted profile cookie store | action/target object or message | 0 / 2 / 3 |
| `cookie browser-import DOMAIN` | browser cookie domain | resolved config, browser cookie database, profile cookie store | merges/saves encrypted profile cookie store | action/target object or message | 0 / 2 / 3 |

`config init` does not accept `--config` or `--profile`; its optional path is
the positional argument.  `config profile init` accepts `--config` as the main
config location and rejects `--profile`, `--data-root`, and `--plugin-root`.
Options which a handler rejects are not “last option wins”: the command exits
with a configuration error before doing its main operation.  The command/option
table above and [option table](#cli-options) together distinguish accepted,
effective, no-op, and rejected options.

<a id="cli-cookie-actions"></a>

### Cookie actions and browser support

`cookie export PATH` and `cookie import PATH` prompt on the controlling terminal
with `Passphrase:`; the passphrase is not accepted as an option or environment
value.  Use `cookie browser-import DOMAIN` only after installing the optional
browser reader: `pip install 'image-downloader[browser-cookies]'`.  Browser
import reads matching browser cookies and merges them into the active profile
store.  None of the three success JSON forms includes cookie values, passphrases,
browser records, or encryption keys.  Encryption/merge/lock behavior is defined
in [runtime behavior](runtime-behavior.md#runtime-cookies).

<a id="cli-examples"></a>

### Common invocations

```powershell
# Download with a named profile and do not overwrite a pre-existing output.
image-downloader download https://example.test/gallery --profile comics --existing-file skip

# Ask an automation consumer for only the changed/new URLs; no images are fetched.
image-downloader download https://example.test/gallery --list-updated-urls --json

# Inspect a site overlay and the selected site plugin without mutation.
image-downloader doctor --host https://example.test/gallery --json

# Create a profile layer, then show exactly which YAML layer won each value.
image-downloader config profile init comics
image-downloader config explain --profile comics --host example.test --json

# Supply an ephemeral plugin config object.  It is not persisted to YAML.
image-downloader download https://example.test/gallery --plugin-config com.example.site='{"page_size": 50}'

# Resolve each image request without fetching an image body. The default emits
# only each effective URL; keep even this signed-URL output local.
image-downloader inspect https://example.test/gallery --plugin com.example.site --json

# Include raw HTTP request material when diagnosing authentication or encoding.
image-downloader inspect https://example.test/gallery --inspection-data http --json

# Include all raw manifest/image metadata and plugin data. Do not archive or share it.
image-downloader inspect https://example.test/gallery --inspection-data all --json

# Show only the manifest; do not call create_image_request().
image-downloader inspect https://example.test/gallery --manifest-only --json

# Select one matching plugin and serialise its image request starts for this operation only.
image-downloader download https://example.test/gallery --plugin com.example.site \
  --plugin-download-policy com.example.site='{"preserve_image_start_order": true}'

# Override a plugin's requested conversion format for this download only.
image-downloader download https://example.test/gallery --force-image-format ORIGINAL
```

For a non-interactive plugin mutation add `--yes`; it does not make `config
init` safer, and it has no confirmation effect there.  An automation client
should treat only stdout as the success JSON channel and retain stderr as
diagnostic output.

## JSON and stream contract

<a id="cli-json"></a>
<a id="cli-success-json"></a>

Every successful `--json` command prints exactly one JSON object to stdout. download chapter logs are suppressed from stdout; diagnostic human errors use stderr. Plugin Python `print()` is redirected to stderr for JSON download. Native code that writes directly to an OS stdout descriptor is outside this control.

| command | success object |
| --- | --- |
| download | `{"saved": string[], "skipped": string[], "failures": ImageFailureJson[]}` |
| download `--list-updated-urls` | `{"updated_urls": string[], "removed": integer}` |
| inspect / download `--inspect-only` | `source_url`, `plugin_id`, `inspection_data: "url"|"http"|"all"`, `request_resolution: "resolved"|"manifest_only"`, `manifest`, `image_requests` |
| config path | `user_config`, `user_config_exists`, `package_baseline`, `default_data_root`, `default_plugin_root` |
| config explain | `source`, `main_config_kind`, `main_config`, `config_root`, `selected_profile`, `target_host`, `layers`, `effective`, `origins`, `runtime_overrides` |
| config init | `{"created_config": string}` |
| config profile init | `{"main_config": string, "created_main_config": boolean, "profile_config": string}` |
| cookie action | `{"operation":"cookie", "action":"export"|"import"|"browser-import", "target":string}` |
| plugin list | `plugin_root`, `plugins`, `diagnostics`, `catalog_warning` |
| plugin install/trust/revoke | normal or content-pin `CatalogEntryJson` |
| plugin uninstall | `plugin_id`, `removed_installation`, `removed_catalog` |
| doctor | `healthy`, `application`, `library`, `plugin_root`, `verification`, `configuration`, `paths`, `loaded_plugins`, `plugins` |

`ImageFailureJson` has `kind` (`fetch|process|save`), `exception`, `message`, `code`, `reason`, `output_path`, `response_url`, `http_status`, `transport`; a non-applicable per-image field is `null`. saved/skipped paths are absolute strings. A failure `output_path`, when it originated below the configured download root, is rendered as a safe relative string rather than an absolute host path; it may also be `null`.

`CatalogEntryJson` normal pin fields are `id, kind, publisher, version, public_key, key_id, manifest_digest, file_tree_sha256, selection_priority, revoked`. Content pin has `id, kind, manifest_digest, content_digest, selection_priority, revoked` only. `diagnostics[]` entries are `{name, source, loaded, detail, warning}`; `warning=true` is not a failed load.

<a id="cli-doctor-json"></a>

`doctor` has a fixed top-level envelope: `healthy: bool`, `application: {version: string, root_directory: string}`, `library: {version: string, root_directory: string}`, `plugin_root: string`, `verification: string`, `configuration: object`, `paths: {name: string}`, `loaded_plugins: object[]`, and `plugins: PluginDiagnosticJson[]`.

`configuration` has exactly `config_file: string|null`, `source: string`,
`main_config_kind: string`, `config_root: string`, `selected_profile: string`,
`target_host: string|null`, `target_has_url_path: bool`, `effective: object`,
`runtime: object`, `selection: object[]`, `layers: object[]`, and `origins:
{dotted_key: source_label}`. `effective` follows the dynamic mapping in the
[configuration schema](configuration.md#config-schema), not a static doctor
sub-schema. `runtime` has `application_overrides: object`, `plugin_overrides:
object`, `fallback_generic: "auto"|"enabled"|"disabled"`, and
`plugin_verification_override: string|null`. Every `layers[]` element is
`{role: string, path: string|null, status: string}`. Each `selection[]` element
is a runtime selection diagnostic (including `id`, `matcher`, and `matched`),
not a catalog entry.

Every `loaded_plugins[]` element has `id`, `kind`, `publisher`, `version`,
`api_version`, `source`, `enabled`, `status`, `match_priority`, `capabilities`,
`entry`, `config_file`, `key_id`, `manifest_digest`, `file_tree_sha256`,
`verification`, `catalog`, `author_defaults`, and `effective_config`. `entry`
is `{file: string, class: string}`; `catalog` is `null` or `{selection_priority,
revoked, key_id, manifest_digest, file_tree_sha256}`. `author_defaults` and
`effective_config` are redacted dynamic mappings. Each `plugins[]` element is
`{name: string, source: string, loaded: bool, detail: string, warning: bool}`;
it includes units that failed before acceptance. Redaction replaces sensitive
values but does not promise a fixed replacement literal. `null` is used only
for the explicitly nullable fields above; handled error fields are omitted when
unknown as described below.

Inspection JSON has `inspection_data` equal to `url`, `http`, or `all`. The default `url` projection has only a manifest count summary plus each request's one-based `chapter_position`, `image_position`, `status`, and AuthFlow/query/cookie-jar 合成後の `effective_url`; an effective-request failure has no guessed URL. `http` adds the complete returned `RequestSpec` (`url`, `method`, `headers`, `cookies`, `referer`, `query`, `form`, `json`, `auth_required`, `retry_non_idempotent`) and `effective_request` (`method`, complete URL, ordered/duplicate-preserving header entries, actual sent cookies, Base64 body and byte count). `all` adds the complete manifest/image projection—including locator, referer, image headers, save options, `original_filename`, and metadata—and raw `plugin_data`. `failed` and `partially_resolved` entries carry stable `failure.code`, `failure.reason`, `failure.exception`, and `failure.phase`; the latter keeps source request data only at `http`/`all`.

`url` can still contain a signed URL query. `http` and `all` are intentionally exceptions to the normal output-safety contract: the material is raw and can contain credentials, cookies, request bodies, signed URLs, or other secrets. Base64 does not protect a body. Do not send inspection stdout or stderr to logs, CI artifacts, telemetry, shared terminals, tickets, or third parties. Normal download/update output and Handled JSON failure use sanitization: an `error` wrapper has required `operation`, `exception`, `code`, `reason`, `message`; `response_url`, `http_status`, `output_path`, `stage`, `transport` are present only when known. An image failure may additionally carry `image_url`; it is a safely rendered manifest locator, not necessarily an HTTP URL. URL query/fragment secrets, cookie contents, passphrases, browser records, and raw credential references never appear in those normal success or failure outputs.

`--inspection-data` changes **only stdout projection**. Except for `--manifest-only`, `url`、`http`、`all` は同じ manifest、`create_image_request()`、`AuthFlow.apply()`、effective request build を実行し、library result には effective request を組み立てられた各画像の raw preview（encoded body を含む）を保持する。したがって `url` は表示の露出を減らすが、request 解決の時間、plugin hook の補助 HTTP、サーバー側副作用、process 内の一時メモリを減らさない。現在は request 解決する画像数、chapter/image 範囲、preview body サイズの CLI 上限を提供しない。大きな manifest または大きな form/JSON body では `--manifest-only` を使うか、site plugin が manifest を有限の operation に分割する。`http`／`all` の Base64 body は元の byte 列より約 33% 大きい stdout を生成し得る。

inspection は registry-only の `doctor` とは異なり、sinkless な runtime を構築して profile cookie snapshot と HTTP gateway を初期化する。通常の output/state/log/notification は実行しないが、設定・plugin verification・filesystem path・cookie store の読取り／lock といったローカル初期化には依存する。matcher 候補だけを副作用最小で確認したいときは `doctor --host URL --json` を使う。

<a id="cli-errors"></a>
