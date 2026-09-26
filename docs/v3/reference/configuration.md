# Configuration reference

<a id="config-layers"></a>

この文書は YAML 設定、layer 解決、path safety、secret reference の正本である。完全なコメント付き雛形は [config-template.yaml](../../../src/image_downloader/config-template.yaml) である。unknown field は Pydantic validation が拒否する。

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
| `output.directory_format`, `output.filename_format` | string; `%NUM%_%TITLE%_%SUBTITLE%`, `%NUM%.%EXT%` |
| `output.existing_file` | `overwrite|skip|rename|error`; `overwrite` |
| `output.image_format` | `JPEG|PNG|WEBP`; `JPEG` |
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
| `plugin_settings.<id>.enabled` | strict boolean; `true` |
| `plugin_settings.<id>.config` | mapping; `{}` |
| `plugin_settings.<id>.secrets` | `lower_snake_case -> UPPERCASE_REFERENCE` mapping; `{}` |
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
| logs | `<profile data root>/logs` | composed download service/logger |
| site and processor units/catalog | `<plugins.root or platform data root/plugins>` | plugin commands and runtime discovery; catalog is `catalog.json` below this root |

The output directory is the profile downloads root followed by
`output.directory_format`; a chapter file name is `output.filename_format`.
Only four tokens are substituted: `%NUM%` (four-digit chapter number or image
index), `%TITLE%`, `%SUBTITLE%`, and `%EXT%` (extension without a leading dot).
The formatter then makes one safe path component, collapses double underscores,
and removes a trailing underscore. It never treats a token value as a path
separator. When `output.isolate_by_plugin=true`, the runtime inserts safe
`<normalized-host>/<selected-plugin-id>/` components between `downloads` and
the formatted chapter directory. `existing_file` controls collisions as described in
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
  directory_format: '%NUM%_%TITLE%_%SUBTITLE%'
  filename_format: '%NUM%.%EXT%'
  existing_file: skip
security: {plugin_verification: strict}

# profiles/comics/app.yaml (profile layer; no profile/storage/plugins)
network: {max_attempts: 4}

# sites/example.test.yaml (site layer; no profile/storage/plugins/security)
plugin_settings:
  com.example.gallery:
    config: {page_size: 50}
```

YAML values may supply persistent configuration only. CLI runtime overrides
(`--existing-file`, `--image-format`, plugin override JSON, and fallback mode)
are applied after the layers for that operation and are not written back. Use
`config explain --json` to see both the effective values and their origins.

<a id="config-plugin-settings"></a>

## Plugin settings and secrets

<a id="config-secrets"></a>

manifest `config_file` が指す author-default YAML（固定名ではない）、persistent `plugin_settings.<id>.config`、operation override はこの順に deep merge する。author-default file の root key は `config` のみである。disabled site plugin は selection candidate ではない。chain 上の disabled processor は error でなく skip される。processor に `secrets` を置くことは configuration error である。

<!-- claim: TAX-CONFIG-SECRETS -->
raw credential を YAML、manifest、catalog、plugin source、log、exception に書かない。logical secret `name` の reference はまず `IMAGE_DOWNLOADER_PLUGIN_<NORMALIZED_ID>_<REFERENCE>` environment variable、次に keyring service `image-downloader.plugin.<plugin-id>` の username `<REFERENCE>` から解決する。どちらにもなければ `SecretNotFound` を送出する。

<a id="config-migration"></a>

## Migration compatibility

v3 は旧 descriptor/tree を discovery しない。network の旧 static key は current model の timeout、retry、pool、response-size fields に置き換え、`config explain --json` の `origins` と `layers` で移管結果を確認する。user-managed YAML の obsolete/unknown static key は `rewrite_user_layers=True` のときだけ削除候補になり、read-only resolve は書換えない。
