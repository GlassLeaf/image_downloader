# Configuration reference

<a id="config-layers"></a>

この文書は YAML 設定、layer 解決、path safety、secret reference の正本である。完全なコメント付き雛形は [config-template.yaml](../../../src/image_downloader/config-template.yaml) である。`AppConfig` / `validate_config` への直接入力では unknown field を拒否する。user-managed YAML の layer 解決では obsolete/unknown static key を検証前に除外し、下記の rewrite 設定によってファイルから削除する場合がある。

<a id="config-discovery"></a>

`DEFAULT_CONFIG` は全 field にこの文書の default を持つ immutable `AppConfig` である。`ConfigurationLayer(role: str, path: Path | None, status: LayerStatus)` は一つの採用/未採用 layer、`ResolvedApplicationConfig(config, main_config_path, config_root, source, selected_profile, layers, origins)` は解決結果である。`source` は `defaults|user|explicit`、`origins` は effective dotted key から source label への read-only mapping である。

## Layers, origins, and mutation

明示 `--config` がなければ CLI は CWD の `app.yaml` を読まない。platform user config がなければ bundled baseline と platform default root を使う。低い方から高い方への merge 順は次のとおりである。

1. `AppConfig` defaults
2. immutable bundled baseline
3. main `app.yaml`
4. `profiles/<profile>/app.yaml`
5. `sites/global.yaml`
6. profile global site layer
7. registrable-domain から full-host までの site layer
8. 同 host の profile site layer
9. CLI bootstrap/application runtime override

mapping は再帰 merge、scalar/list/`null` は高い layer が置換する。IDNA、registrable domain、IPv4/IPv6 filename の正規化、検討した layer、値の origin は `config explain --json` で確認する。

| layer role | `profile` | `storage` | `plugins` | `security` |
| --- | --- | --- | --- | --- |
| bundled baseline / main `app.yaml` | allowed | allowed | allowed | allowed |
| `profiles/<name>/app.yaml` | forbidden | forbidden | forbidden | allowed |
| any `sites/*.yaml` (global and host, profile scoped を含む) | forbidden | forbidden | forbidden | forbidden |

違反は layer を黙って無視せず `ConfigurationError` にする。これは bootstrap root と verification policy を site ごとに切替えて security boundary を曖昧にしないためである。

## Initialization commands

`config init [ABSOLUTE_PATH]` は引数なしなら platform user config、引数ありなら absolute path に main config を一回だけ作る。`--config` と `--profile` は拒否され、`--data-root` と `--plugin-root` は作成する main config の bootstrap values にだけ書き込む。既存 file は上書きしない。`--yes` は受理されるが confirmation を使わない。

`config profile init NAME` の `NAME` は `[A-Za-z0-9_-]+` である。`--config` は main config の absolute path として受理し、存在しなければ main config も作成する。profile file は `<config-root>/profiles/NAME/app.yaml` に一回だけ作る。profile init では `--data-root`、`--plugin-root`、`--profile` は拒否され、`--yes` は no-op である。作成された profile layer には上表の profile-layer 制約が適用される。

<!-- claim: TAX-CONFIG-REWRITE -->
`resolve_application_config(..., rewrite_user_layers=True)` と `load_application_config(..., rewrite_user_layers=True)` は obsolete/unknown static key を user-managed YAML から atomic に削除し得る。default は `False` で read-only である。download、update listing、cookie operation、plugin mutation は initial config の作成と rewrite を試みる。initial config の作成に失敗しても root を解決済みなら主 operation の終了状態は維持し、safe warning を stderr に出す。`doctor`、`config path`、`config explain`、plugin list は file/directory を作成・書換えしない。

既存 config、overlay、data/plugin root、managed output は symlink または Windows reparse point を含められない。不安全な path を「欠落」として無視せず `ConfigurationError` または storage safety failure にする。

<a id="config-schema"></a>

## Schema

| key | type / default / constraint |
| --- | --- |
| `profile.default` | `[A-Za-z0-9_-]+` string; `default` |
| `storage.data_root`, `plugins.root` | `null` または absolute path string; `null` は platform root |
| `download.chapter_concurrency` | strict integer `>=1`; `3` |
| `download.image_concurrency_per_chapter` | strict integer `>=1`; `8` |
| `download.continue_on_image_error`, `download.allow_empty_chapter_manifest` | strict boolean; `true`, `false` |
| `output.directory_format`, `output.filename_format` | string; `%CHAPTER_NUMBER%_%CONTENT_TITLE%_%CHAPTER_TITLE%`, `%IMAGE_INDEX%.%EXT%`。`%NUM%`、`%TITLE%`、`%SUBTITLE%` は configuration error |
| `output.existing_file` | `overwrite|skip|rename|error`; `overwrite` |
| `output.image_format` | `ORIGINAL|JPEG|PNG|WEBP`; `ORIGINAL`。`ORIGINAL` は plugin transform/processor 完了後の artifact bytes を core が再エンコードせず保存する |
| `output.isolate_by_plugin` | strict boolean; `false` |
| `output.max_component_length` | `null` or strict integer `>=16`; `null` |
| `output.lock_timeout_seconds` | finite number `>=0`; `30`; zero は待機しない |
| `media.input_validation` | `content_type|decode|both`; `content_type` |
| `media.content_type_mismatch` | `accept|error`; `accept` |
| `media.max_image_pixels` | `null` or strict integer `>=1`; `null` |
| `logging.console.enabled` | strict boolean; `true` |
| `logging.safe_query_parameters`, `logging.safe_fragment_parameters` | unique non-sensitive identifier list; `[]` |
| `network.request_concurrency` | strict integer `>=1`; `8` |
| `network.origin_request_concurrency`, `network.registrable_domain_request_concurrency` | `null` or strict integer `1..network.request_concurrency`; `null` |
| `network.request_timeout_seconds`, `network.connect_timeout_seconds`, `network.read_timeout_seconds`, `network.write_timeout_seconds`, `network.pool_timeout_seconds` | request `>0` finite, default `30`; others `null` or finite `>0` and inherit `network.request_timeout_seconds` |
| `network.max_attempts` | strict integer `>=1`; `3` including initial request |
| `network.retry_max_delay_seconds` | finite number `>=0`; `30` |
| `network.auth_refresh_attempts` | strict integer `>=0`; `1` additional refresh |
| `network.global_request_interval_seconds` | finite number `>=0`; `0` |
| `network.pool_max_connections`, `network.pool_max_idle_connections` | strict integer `>=1` / `0..network.pool_max_connections`; `8` / `8` |
| `network.max_response_bytes` | strict integer `>=1`; `67108864` |
| `network.http2`, `network.follow_redirects` | strict boolean; both `true` |
| `network.proxy` | `null` or string; `null` |
| `network.headers` | string mapping; `{"User-Agent": "image-downloader/0.0.0.1b0"}` |
| `notification.enabled` | strict boolean; `false` |
| `notification.methods` | `desktop|email` list; `[desktop]` |
| `notification.notify_on` | category list; default `fetch_error, process_error, save_error, auth_error, config_error, plugin_error, update_error, storage_error, runtime_error` |
| `notification.routes` | category-to-method-list mapping; `{}` |
| `notification.desktop` | mapping; `{}` |
| `notification.email.smtp_host`, `notification.email.from`, `notification.email.username` | string; `''` |
| `notification.email.smtp_port` | strict integer `1..65535`; `465` |
| `notification.email.use_tls` | strict boolean; `true` |
| `notification.email.to` | string list; `[]` |
| `notification.email.credential_service` | string; `image-downloader.smtp` |
| `security.plugin_verification` | `strict|warn|off`; `strict`; main/profile app layer only |
| `image_processors.chain` | unique reverse-DNS processor IDs; `[]` |
| `image_processors.transport_metadata_access` | processor ID -> non-empty, duplicate-free enabled non-builtin site plugin ID list mapping; `{}`。chain に含まれる processor と列挙された site の組だけが raw image transport metadata を受け取る |
| `plugin_settings.<id>.enabled` | strict boolean; `true` |
| `plugin_settings.<id>.config` | mapping; `{}` |
| `plugin_settings.<id>.secrets` | `lower_snake_case -> UPPERCASE_REFERENCE` mapping; `{}` |
| `plugin_settings.<id>.download_policy.request_concurrency` | `null` or strict integer `>=1`; selected operation の HTTP transport 上限。実効値は `network.request_concurrency` との最小値 |
| `plugin_settings.<id>.download_policy.chapter_concurrency` | `null` or strict integer `>=1`; 実効値は `download.chapter_concurrency` との最小値 |
| `plugin_settings.<id>.download_policy.image_concurrency_per_chapter` | `null` or strict integer `>=1`; 実効値は `download.image_concurrency_per_chapter` との最小値 |
| `plugin_settings.<id>.download_policy.preserve_image_start_order` | strict boolean; `true` なら chapter/image job の開始を manifest 順に直列化する。default `false` |
| `fallback.generic_html.enabled` | strict boolean; `true` |

`NotificationCategory` の全 literal は `fetch_error`、`process_error`、`save_error`、`auth_error`、`parse_error`、`plugin_error`、`config_error`、`storage_error`、`runtime_error`、`update_error`、`auth_cookie_store_access`、`auth_credential_store_access`、`auth_login_success`、`auth_session_refresh_success`、`download_success`、`download_partial_success` である。category が設定可能であることと core が自動発火することは同義ではない。[observability explanation](../explanation/observability.md) を参照する。

<a id="config-storage-layout"></a>

## Inputs, storage layout, and output names

The configuration file itself is an input, not a storage root.  `storage.data_root`
and `plugins.root` accept only an absolute path or `null`; `null` selects the
platform default.  Relative paths, a path whose existing component is a
symlink/reparse point, and a root that cannot be safely resolved are rejected.
`resolve_paths(config)` derives the following paths from the selected profile:

| purpose | location | reader/writer |
| --- | --- | --- |
| main configuration | explicit `--config`, otherwise platform user config `conf/app.yaml` | all commands that resolve configuration; state-changing commands can bootstrap it |
| profile data root | `<storage.data_root or platform data root>/profiles/<profile>` | resolved by all runtime operations |
| downloaded images and chapter reports | `<profile data root>/downloads` | normal `download` writes; `--list-updated-urls` does not allocate image output |
| encrypted cookie jar and lock | `<profile data root>/cookie/cookies.enc`, `cookie/cookies.lock` | cookie actions and composed download service |
| update snapshots and lock | `<profile data root>/state/updates.json`, `state/updates.lock` | update listing and `DownloadService.check_updates()` |
| workflow completion state | `<profile data root>/state/workflow.json`, `state/workflow.lock` | normal workflow; offline state display reads it |
| workflow execution history and lock | `<profile data root>/state/workflow-history.json`, `state/workflow-history.lock` | normal workflow auto-records; offline display reads; prune organizes history |
| logs | `<profile data root>/logs` | composed download service/logger |
| site and processor units/catalog | `<plugins.root or platform data root/plugins>` | plugin commands and runtime discovery; catalog is `catalog.json` below this root |

The output directory is the profile downloads root followed by
`output.directory_format`; a chapter file name is `output.filename_format`.
`directory_format` accepts `%CHAPTER_NUMBER%` (the four-digit
`Chapter.number`), `%CONTENT_TITLE%` (`DownloadManifest.title`),
`%CHAPTER_TITLE%`, `%CHAPTER_SUBTITLE%`, and the existing `%EXT%` expansion
(`jpeg`). `filename_format` accepts those content/chapter tokens plus
`%IMAGE_INDEX%` (the four-digit `ImageResource.index`) and `%EXT%` (the saved
artifact extension without a leading dot). It alone also accepts
`%ORIGINAL_STEM%`, `%ORIGINAL_FILENAME%`, and `%ORIGINAL_EXT%`: the selected
source filename without its last extension, the complete selected source
filename, and that last source extension without its leading dot.

`%NUM%`, `%TITLE%`, and `%SUBTITLE%` are no longer accepted in either format.
Migrate them to the explicit content, chapter, or image token matching the
intended level. `%IMAGE_INDEX%` and the `%ORIGINAL_*%` image-specific tokens in
`directory_format` are configuration errors. `%CHAPTER_NUMBER%` is available
in a file name so that, for example, `%CHAPTER_NUMBER%_%IMAGE_INDEX%.%EXT%`
can identify both levels.

Any participating plugin can return operation-stable non-secret values through
the optional `OutputFormatValueProvider.output_format_values()` hook. A value
is referenced as `%PLUGIN[com.example.gallery:SERIES_ID]%`; the plugin returns
only `{"SERIES_ID": "..."}` and core owns the reverse-DNS ID namespace. Plugin
tokens are valid in both formats. The hook is collected once before inspection
and its frozen values are shared by every chapter and image. A plugin that does
not implement the hook, a disabled/unselected plugin ID, or an absent key is
not an error: the token is retained literally before normal component safety
conversion. This deliberately makes typos and removed plugin keys visible in
the output name rather than failing a download. Plugin values are not expanded
recursively and must not contain secrets. Invalid `%PLUGIN[...]%` syntax is a
configuration error; other unrecognized percent-delimited text remains
literal for compatibility.

The selected source filename is, in order: a site plugin's non-secret
`ImageResource.original_filename`; successful response `Content-Disposition`
(`filename*` before `filename`); final response URL path; absolute manifest
locator URL path; then the four-digit image index. URL query/fragment values do
not participate. `filename*` follows RFC 8187 decoding. The stem and original
extension always come from that one selected name; if it has no extension,
`%ORIGINAL_EXT%` is empty and core does not infer an extension from MIME data,
Pillow, or a lower-priority name. Use `%ORIGINAL_STEM%.%EXT%` when the saved
format may differ from the source; `%ORIGINAL_FILENAME%` intentionally keeps
the source suffix and may therefore not match transformed bytes.

The formatter then makes one safe path component, collapses double underscores,
and removes a trailing underscore. It never treats a token value as a path
separator. When `output.isolate_by_plugin=true`, the runtime inserts safe
`<normalized-host>/<selected-plugin-id>/` components between the image-output
root and the formatted chapter directory. The default root is `downloads`; a
normal download's `--output-dir ABSOLUTE_PATH` replaces only that root, not the
profile cookie/log/state roots. `existing_file` controls collisions as described in
[runtime behavior](runtime-behavior.md#output-allocation-and-locks).

The following is a complete, valid *shape* for a main config plus a profile and
site overlay. It intentionally shows only keys permitted in each layer; the
commented [config template](../../../src/image_downloader/config-template.yaml)
is the full default/value reference.

```yaml
# app.yaml (main layer)
profile: {default: comics}
storage: {data_root: 'D:/image-data'}
plugins: {root: 'D:/image-plugins'}
output:
  directory_format: '%CHAPTER_NUMBER%_%CONTENT_TITLE%_%CHAPTER_TITLE%'
  filename_format: '%IMAGE_INDEX%.%EXT%'
  existing_file: skip
security: {plugin_verification: strict}

# profiles/comics/app.yaml (profile layer; no profile/storage/plugins)
network: {max_attempts: 4}

# sites/example.test.yaml (site layer; no profile/storage/plugins/security)
plugin_settings:
  com.example.gallery:
    config: {page_size: 50}
    download_policy:
      image_concurrency_per_chapter: 1
```

YAML values may supply persistent configuration only. CLI runtime overrides
(`--existing-file`, `--image-format`, `--directory-format`, plugin config/policy override JSON, and fallback mode)
are applied after the layers for that operation and are not written back.
`--output-dir` is a separate download-only composition override rather than an
`AppConfig` field, so it also is not written back. Use
`config explain --json` to see supported configuration overrides and their
origins; it does not accept download-only `--directory-format` or `--output-dir`.

画像の保存形式は次の優先順位で決まる。

| priority | source | effect |
| --- | --- | --- |
| 1 | `DownloadService.run(force_image_format=...)` / CLI `--force-image-format` | plugin 指定も上書きする。`--image-format` とは排他的。 |
| 2 | plugin の `ImageResource.save_options.format` | 画像単位の保存形式。 |
| 3 | CLI `--image-format` または YAML の `output.image_format` | plugin 指定がないときの既定。CLI はその operation 限り。 |
| 4 | bundled default `ORIGINAL` | 上位の指定がない新規設定の既定。 |

`ORIGINAL` は HTTP 応答の raw-download モードではなく、site transform と processor
chain 後の最終 artifact を core が decode/re-encode せず保存するモードである。
`media.input_validation` は維持され、processor 完了後の artifact も検証する。extension
は artifact、Content-Type、locator、`.bin` の順で決める。

<a id="config-plugin-settings"></a>

## Plugin settings and secrets

<a id="config-secrets"></a>

manifest `config_file` が指す author-default YAML（固定名ではない）、persistent `plugin_settings.<id>.config`、operation override はこの順に deep merge する。author-default file の root key は `config` のみである。disabled site plugin は selection candidate ではない。chain 上の disabled processor は error でなく skip される。processor に `secrets` を置くことは configuration error である。

`core.generic-html` は通常の site plugin ではなく、外部 plugin が一致しないときだけ選ばれる組み込み fallback である。この ID を `plugin_settings`、`--plugin-config`、`--plugin-config-file`、`--plugin`、`--force-plugin` の対象にはできず、指定は configuration error になる。generic fallback が自動選択された download operation に限り、core 所有の一回限りの `--plugin-download-policy core.generic-html=<JSON>` または `--plugin-download-policy-file` は利用できる。これは persistent 設定ではなく、画像抽出や request/auth の振る舞いを変更しない。fallback の有効/無効は `fallback.generic_html.enabled` または `--fallback-generic` で制御する。

`plugin_settings.<id>.download_policy` は plugin private `config` と別の、core 所有の operation 制御である。`request_concurrency` は selected operation 内の transport/retry を、chapter/image concurrency は image job scheduler を制限する。いずれも resolved global/host setting と最小値を採用する。`preserve_image_start_order: true` は chapter と image concurrency をともに `1` にする。plugin は `config.network.request_concurrency` を読めるが、それだけでは core scheduler を変更できない。CLI の一回限りの policy override は [CLI options](cli.md#cli-options) を参照する。

`image_processors.transport_metadata_access` は、selected site plugin の画像 fetch に関する raw request/response metadata を processor へ渡すための明示 allow-list である。key は enabled な `image_processors.chain` の processor ID、value は enabled かつ non-builtin な site plugin ID の重複なし配列にする。wildcard は使えない。未登録の processor/site、chain 外 processor、builtin/disabled site は configuration error である。許可されない processor も transform ごとに同型の metadata を受けるが、URL query、header/cookie 値、plugin data は redacted される。これは persistent config だけで指定でき、CLI/runtime の一時 override はない。

```yaml
image_processors:
  chain:
    - com.example.watermark
  transport_metadata_access:
    com.example.watermark:
      - com.example.gallery
```

<!-- claim: TAX-CONFIG-SECRETS -->
raw credential を YAML、manifest、catalog、plugin source、log、exception に書かない。logical secret `name` の reference はまず `IMAGE_DOWNLOADER_PLUGIN_<NORMALIZED_ID>_<REFERENCE>` environment variable、次に keyring service `image-downloader.plugin.<plugin-id>` の username `<REFERENCE>` から解決する。どちらにもなければ `SecretNotFound` を送出する。

<a id="config-migration"></a>

## Migration compatibility

v3 は旧 descriptor/tree を discovery しない。network の旧 static key は current model の timeout、retry、pool、response-size fields に置き換え、`config explain --json` の `origins` と `layers` で移管結果を確認する。user-managed YAML の obsolete/unknown static key は `rewrite_user_layers=True` のときだけ削除候補になり、read-only resolve は書換えない。
## Workflow実行履歴の保持設定

```yaml
workflow_history:
  max_age_days: 90
  max_size_bytes: 104857600
```

`workflow_history.max_age_days`と`workflow_history.max_size_bytes`は正の整数。既定はprofile全体で90日・100MiB。容量は`state/workflow-history.json`全体のUTF-8保存サイズに適用し、成果物、debug.log、ロック、一時ファイルを含めない。

通常workflowはCLI/APIともに複数回の実行結果を自動保存する。dry-runは記録しない。既存`state/workflow.json`はschema 1の最新候補・URL単位完了管理を維持し、履歴ファイルも別のschema 1とする。履歴は終了日時から保持期間を判定し、境界を含む期限切れを表示から除外するが、表示ではファイルを変更しない。新規記録時と明示的な`state workflow prune`で物理削除する。

通常保存では期限切れを削除し、その後は古い実行から容量整理する。ただし新しい単一結果だけで容量上限を超える場合、その結果を省略せず保存し、期間内の既存履歴も容量理由では削除しない。上限超過と整理の推奨を警告する。明示的pruneは期限切れを先に削除し、古い順に容量整理するが、期間内の最後の1件は残す。期限切れなら最後の1件も削除できる。

容量は例外を認める上限であり、厳密な最大サイズではない。最後の1件が大きすぎる場合は上限変更または履歴退避が必要。`prune --dry-run`は削除予定と整理前後の容量を表示し、本整理時には共有ロック下で再計算する。設定変更は次の読込み・保存・整理から適用する。
