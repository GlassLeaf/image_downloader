# CLI reference

<a id="cli-forms"></a>

Canonical command forms are:

```text
image-downloader download URL [--force-image-format FORMAT] [options]
image-downloader workflow URL [--download-scope all|updated] [download options]
image-downloader state workflow [list|show|history|run|prune] [arguments] [options]
image-downloader inspect URL [options]
image-downloader download URL --inspect-only [options] # inspect の alias
image-downloader URL [download options]                 # bare-URL compatibility form
image-downloader doctor [--host HOST_OR_URL] [options]
image-downloader config path|explain|init [arguments] [options]
image-downloader config profile init NAME [options]
image-downloader plugin list|install|trust|revoke|uninstall [arguments] [options]
image-downloader cookie export|import|browser-import VALUE [options]
image-downloader help [COMMAND [SUBCOMMAND ...]]
```

legacy `--export-cookies`、`--import-cookies`、`--import-browser-cookies` select one cookie action and cannot be combined. `URL` bare form is rewritten to `download`.

<a id="cli-state-effects"></a>

config 作成・rewrite の command ごとの副作用は [configuration reference](configuration.md#config-layers) が正本である。`config path` と `config explain` は観測専用である。

## Option acceptance and effect

### Workflow

`workflow URL --dry-run` performs one live feed check and previews initial-round URL selection.
It accepts `--download-scope all|updated` and existing workflow options, including options before the command.
`--dry-run` is also accepted by `state workflow prune`; all other commands and the bare-URL download form reject it. Existing option validation and
exclusions remain; retry options are validated but no waits or additional rounds run.

The preview displays selected/excluded candidates and reasons (`all`, `added`, `changed`, `unfinished`,
or excluded `completed`), removed candidates, and candidate/selected-URL counts. URLs are deduplicated for
selection in feed order; candidates sharing a URL share its selection and reasons.
It does not inspect target manifests, fetch/process/save images, predict image counts/output filenames/file
conflicts, or prove that downloads will succeed. Output/format/existing-file options are accepted but not simulated.

The command does not update `updates.json` or `workflow.json`, initialize/rewrite configuration, save cookies
or file logs, or send notifications. Existing cookies are read into a detached session; authentication refresh
and ordinary HTTP retries can occur, but session changes are discarded. Lock files and their parent directories
may be created. Feed/plugin authentication, check and cleanup code actually runs; plugin-defined external
side effects are not sandboxed. The next real workflow checks the feed again; a preview does not reserve its list.

With `--json`, one safe document contains `operation="workflow"`, `dry_run=true`, `source_url`,
`download_scope`, `plugin_id`, `first_run`, `checked_at`, `candidates`, `changes`, `selected_urls`, `items`,
`summary`, `stop_error`, and `cancelled`. Items contain `candidate`, `selected`, and `reasons`; summary counts
`candidates`, `selected_urls`, `selected_candidates`, `excluded_candidates`, and `removed_candidates`.
`first_run` is null until history comparison finishes, otherwise indicates that this plugin/feed has no workflow history.
Failures retain confirmed preview information where available. Plugin stdout goes to stderr in preview mode.
Successful previews, including empty selection, return 0; errors retain existing exception-specific codes and
user cancellation returns 130. A preview never returns download partial-success code 5.

```sh
image-downloader workflow https://example.test/feed --dry-run
image-downloader --dry-run workflow https://example.test/feed --download-scope all --json
```

`workflow URL` checks the selected update provider, then downloads selected URLs sequentially.

`core.generic-html` also supports workflow: the only candidate is the requested page itself, not linked
pages. Its revision compares the ordered extracted image URL/index/image_id list (duplicates included),
ignoring title/body/save settings. URL query or ordering changes count as updates; changed image bytes
at unchanged URLs cannot be detected. HTML is fetched once for the check and again for each selected
page manifest; retry image retention uses the existing rules. A page with zero images is still a candidate
and succeeds, matching normal download's one empty chapter. The empty-manifest setting controls zero
chapters. An update-incompatible plugin reports `update_check_unsupported`, with exit code 4.

```text
image-downloader workflow https://example.test/gallery --fallback-generic enabled
image-downloader workflow https://example.test/gallery --fallback-generic enabled --dry-run --json
```
`--download-scope updated` is the default: select added/changed candidates and unfinished candidates still present.
`--download-scope all` selects the entire current snapshot. The first workflow run selects all candidates.
Removed candidates are never downloaded; a removed candidate that reappears is added again.
Comparison uses workflow history, independently of `--list-updated-urls` history.

Configuration/profile/root/security, plugin configuration/selection/fallback/download-policy, output-directory,
directory-format, existing-file, image-format/force-image-format, JSON and console options apply to workflow.
Existing precedence and exclusions remain: image-format versus force-image-format; plugin versus force-plugin;
explicit plugin selection versus explicit fallback. Explicit plugin selection applies to the feed and every target.
Site configuration is resolved for the feed and shared across targets, including cookies and connections.
`--yes` is accepted without introducing another confirmation.
`--download-scope` is accepted before/after `workflow`, and rejected by other commands.
Update-listing, inspection, cookie actions, `--host` and `--selection-priority` are rejected by workflow,
including when supplied before the command.

Output/configuration changes do not themselves make a completed candidate updated: use `all` to download again
with new output settings. Existing-file policy is unchanged, including fetching/processing before `skip`.
An unfinished URL with a retryable failure is retried after the initial round. Each retry round refreshes
the feed and target manifests, removes disappeared URLs, and processes remaining failures plus new/changed URLs.
Unchanged successful/skip image outcomes are retained within this invocation, including with scope `all`.
Next invocation still uses URL-level unfinished state and ordinary existing-file policy.

URL候補の更新判定と、対象URL内の画像の変更判定は別である。画像は`ImageResource`の全フィールドを
比較し、同じ`image_id`でも画像URLのクエリ、`referer`／`headers`、`save_options`、`original_filename`
などが変われば再取得する。画像本体や実際の要求URLは比較しない。
条件表と既知の制限は[runtime behavior](runtime-behavior.md#workflow-retry-rounds)、
具体例は[workflow FAQ](../how-to/faq.md#workflow-retry-faq)を参照する。

| workflow-only option | default | behavior |
| --- | --- | --- |
| `--workflow-retries` N | 1 | Additional rounds; non-negative integer. 0 disables retries. |
| `--workflow-retry-delay` SECONDS | 600 | Fixed wait before each retry round; finite, non-negative number. |
| `--workflow-retry-timeout` SECONDS | unlimited | Positive finite retry duration after the initial round, including waits/checks/downloads. |

These options work before/after `workflow` and are rejected by other commands. No configuration-file keys are added.
All-success runs finish immediately without waiting or watching for further updates. Timeout cancels active work,
then finishes cleanup and started writes; shutdown may exceed the requested duration. The same-feed process lock
remains held during the 10-minute default wait. Changed images still use the existing-file policy:
`rename` can create additional files and `error` can fail on an existing output.

```sh
image-downloader workflow https://example.test/feed --workflow-retries 2 --workflow-retry-delay 30 --workflow-retry-timeout 300 --json
image-downloader workflow https://example.test/feed --workflow-retries 0
```

Retryable failures include transport errors, authentication, plugin failures and image processing errors.
Missing secrets, unsupported features/formats, redirect/size/dimension limits, closed processors, permanent HTTP
4xx responses (except 401/403/408/429), storage failures and file conflicts are excluded. 5xx is retryable.
Configuration/state/lock errors and unexpected internal exceptions stop the workflow. HTTP-level retries remain active.

JSON emits one document containing `operation`, `source_url`, `download_scope`, `checked_at`, `candidates`,
`changes`, `selected_urls`, `items`, `summary`, `stop_error`, and `cancelled`.
Each item contains safe `url`, `reasons`, `status`, `download` and `error`;
download reuses `saved`, `skipped`, `failures`. Reasons are `all`, `added`, `changed`, `unfinished`.
Statuses are `success`, `partial`, `failed`, `unprocessed`, `removed`; summary counts these statuses.
JSON additionally includes retry settings, `rounds`, per-item `attempts`, and `timed_out`.
`outcome` distinguishes `success`, `incomplete`, `stopped`, `timed_out`, and `cancelled`
(cancellation takes precedence over timeout, then stop, then incomplete URL results).
Normal output labels counts as `URL results` separately from the overall outcome. A stop before
the snapshot is established says `update check result: not established`; zero failed URLs does not imply success.
Rounds expose snapshots/differences/selected/removed URLs and stop information; the top-level snapshot and changes
refer to the latest successfully prepared round. Attempts expose merged download results and image positions,
safe locators/paths, status, `attempted`, and `retained`. Round 0 is the initial round.
Intermediate failures remain in logs/events/JSON; download notifications contain only final results.
Stopped workflows retain completed results and unprocessed targets. Diagnostics go to stderr in JSON mode.
Success or no selected targets returns 0; image/URL failures with saved/skipped files return 5;
failures without saved/skipped files return 1. Fatal stops use existing exception codes, cancellation uses 130.

<a id="cli-options"></a>

`--json` is accepted by every command. **Accepted** does not imply an effect. Incompatible options raise `ArgumentError` before configuration resolution. Options can appear before the command, between subcommands, or after operands; acceptance and diagnostic precedence are the same at each position. Only complete option names and documented aliases are accepted; abbreviations such as `--j` and `--list` are rejected.

Scalar options use the last supplied value. Repeated JSON/file options retain their order, with all file overrides merged before inline overrides. Explicit defaults still count as supplied options: `plugin list --selection-priority 0` is invalid. `--` ends option recognition; following strings are positional operands.

Bare operands select download only when they are absolute HTTP(S) URLs with a host. The same URL requirement applies to explicit download, inspect, and workflow commands. Other words are unknown commands.

`help`, `help plugin`, and `help config profile` show the corresponding help and exit 0. Unknown or excess help targets are argument errors. `--help` and `-h` stop parsing early; a missing option value before help remains an error. Help is always text on stdout, including with `--json`, and does not initialize configuration, plugins, or Cookie stores.

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
| `--output-dir ABSOLUTE_PATH`, `--directory-format FORMAT` | normal download and bare-URL download | `download --inspect-only` と `--list-updated-urls` では accepted/no effect。inspect、doctor、config、plugin、cookie では rejected |
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

`--output-dir` replaces only the image-output root for one normal download; it must be an absolute safe directory path and does not relocate profile logs, cookies, or update state. `--directory-format` is the corresponding one-operation override of `output.directory_format`, with the same token validation. They can be combined: `--output-dir C:\Base --directory-format destination_%CHAPTER_NUMBER%` saves beneath `C:\Base\destination_0001`. If `output.isolate_by_plugin=true`, core instead inserts `<normalized-host>/<selected-site-plugin-id>/` between the selected output root and the formatted chapter directory. Neither option writes YAML.

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
with an argument error before doing its main operation.  The command/option
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
## Workflow状態表示・履歴・整理

```text
image-downloader state workflow
image-downloader state workflow list
image-downloader state workflow show URL [--plugin ID]
image-downloader state workflow history [URL] [--plugin ID] [--limit N]
image-downloader state workflow run RUN_ID
image-downloader state workflow prune [--dry-run]
```

`--config`、`--profile`、`--data-root`、`--json`はコマンド前後で指定できる。`--limit`はhistory専用の正の整数で、既定20件。`--plugin`はlist/show/historyで保存済みplugin IDを絞り込み、プラグインのインストール・検証・選択をしない。同じURLが複数pluginに属すると、指定なしではそれぞれ表示する。ダウンロード・再試行・inspection・Cookie操作・出力設定などの実行オプションは拒否する。`--dry-run`は通常workflowと`state workflow prune`のみで受理する。

ネットワーク通信・プラグイン読込み・Cookie読込み・設定の初期保存/rewrite・ファイルログ・通知は行わない。ロックファイルと親ディレクトリの作成は許容する。一度だけ保存情報を読み取り、進捗の自動更新、実行中判定、成果物検証は行わない。

現在状態は候補のURL、content ID、revision、完了フラグ、チェック日時と集計を持つ。未完了を失敗・未処理・実行中と推測しない。過去結果は別欄で表示し、過去の成功は現在の完了を保証しない。feedはplugin IDとSHA-256キーで識別する。履歴があれば伏字済み更新元URLを表示するが、既存の完了状態だけからURLを復元・推測しない。

listはfeedの概要、showは現在候補と最新実行の詳細を別欄で表示する。現在状態が未作成なら`current state: not available`と表示し、存在する空一覧の`candidates=0`と区別する。historyは実行ごとの概要、runはURL別結果・試行・周回・停止原因を整形して表示する。通常表示では辞書やJSONをそのまま出力しない。古い履歴の`plugin_error`から原因を推測せず、保存されたコードと安全な理由を表示する。

`state workflow list`に表示する実行結果は各feedの最新1件のみであり、過去の履歴を削除したことを意味しない。通常表示にもこの制限と履歴確認コマンドを案内する。過去の保存履歴は`py -m image_downloader state workflow history`で全feed分、`py -m image_downloader state workflow history URL`で特定URL分を確認する。既定は新しい順に20件で、`--limit N`で表示件数を変更できる。

JSONは`operation="state"`、`resource="workflow"`、`action`を持つ一文書。list/showは`workflows`、historyは`runs`、runは`run`、pruneは`prune`を返す。pruneは削除予定RUN_ID、期限切れ件数、UTF-8ファイルサイズの整理前後、上限超過の有無を返す。URL・エラー・パスは既存の伏字処理を適用する。

list/showの各feedには`state_available`を追加する。falseでも互換性のため`checked_at=null`、空の`items`、0件の`summary`は維持する。この0件は「保存済みの空一覧」を意味しない。

表示・整理の成功は0（過去の失敗を表示しても0）、明示したURL/RUN_IDが存在しない場合は1、設定不正は2、保存情報の破損・ロック・I/O失敗は1、キャンセルは130。空の一覧・履歴一覧は正常終了する。通常workflowのJSONには`run_id`、`history_saved`、`history_warning`を追加する。履歴保存失敗は警告し、元のworkflow終了コードを変更しない。
