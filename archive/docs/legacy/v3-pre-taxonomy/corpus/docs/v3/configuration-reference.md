# 設定参照

<a id="config-layers"></a>

この文書は YAML configuration、layer 解決、型・既定値・migration の正本である。完全なコメント付き雛形は [config-template.yaml](../../src/image_downloader/config-template.yaml) であり、ここにない key を追加してはならない。Pydantic model は unknown field を拒否する。

## Discovery and merge

<a id="config-discovery"></a>

通常 CLI は CWD の `app.yaml` を読まない。明示 `--config` がなければ platform user config を探し、なければ bundled baseline と OS default roots で解決する。`storage.data_root` と `plugins.root` は `null` または absolute path だけを受け、`null` は platform root を意味する。

低い方から高い方へ、次をこの順に merge する。

1. core `AppConfig` defaults
2. immutable bundled baseline
3. config root の `app.yaml`
4. `profiles/<profile>/app.yaml`
5. `sites/global.yaml`
6. `profiles/<profile>/sites/global.yaml`
7. registrable domain から full host までの `sites/<host>.yaml`
8. 同じ host の profile site layer
9. CLI bootstrap/application runtime overrides

mapping は再帰 merge、scalar/list/`null` は高い layer が置換する。`profile:`、`storage:`、`plugins:`、`security:` は許される layer が限定される。`config explain --json` の `layers` は検討した layer と状態、`origins` は有効値の由来を表示する。host normalization は IDNA A-label、registrable domain、IPv4 exact match、IPv6 の安全な filename を考慮する。

<a id="config-rewrite"></a>

`resolve_application_config(..., rewrite_user_layers=True)` と `load_application_config(..., rewrite_user_layers=True)` は obsolete または unknown static key を user-managed YAML から原子的に除去し得る。既定値は `false` で読み取り専用である。状態変更 CLI は initial config を作成・必要な rewrite を行い得るが、`doctor`、`config path`、`config explain`、`plugin list` は file/directory を作成・書換えしない。

## YAML schema

<a id="config-schema"></a>

| key | type, default, constraints |
| --- | --- |
| `profile.default` | string `[A-Za-z0-9_-]+`; `default` |
| `storage.data_root` | `null` or absolute path string; `null` |
| `plugins.root` | `null` or absolute path string; `null` (data root の `plugins`) |
| `download.chapter_concurrency` | strict integer `>= 1`; `3` |
| `download.image_concurrency_per_chapter` | strict integer `>= 1`; `8` |
| `download.continue_on_image_error` | strict boolean; `true` |
| `download.allow_empty_chapter_manifest` | strict boolean; `false` |
| `output.directory_format`, `filename_format` | string; `%NUM%_%TITLE%_%SUBTITLE%`, `%NUM%.%EXT%` |
| `output.existing_file` | `overwrite|skip|rename|error`; `overwrite` |
| `output.image_format` | `JPEG|PNG|WEBP`; `JPEG` |
| `output.isolate_by_plugin` | strict boolean; `false` |
| `output.max_component_length` | `null` or strict integer `>= 16`; `null`; enabled truncation is deterministic with a hash suffix |
| `output.lock_timeout_seconds` | finite number `>= 0`; `30`; `0` does not wait. Lock failure aborts the operation rather than becoming an image retry. |
| `media.input_validation` | `content_type|decode|both`; `content_type` |
| `media.content_type_mismatch` | `accept|error`; `accept` |
| `media.max_image_pixels` | `null` or strict integer `>= 1`; `null`; applies to input and processor output |
| `logging.console.enabled` | strict boolean; `true` |
| `logging.safe_query_parameters`, `safe_fragment_parameters` | unique non-sensitive identifier list; `[]` |
| `network.request_concurrency` | strict integer `>= 1`; `8` |
| `network.origin_request_concurrency`, `registrable_domain_request_concurrency` | `null` or strict integer `1..request_concurrency`; `null` inherits global |
| `network.request_timeout_seconds` | finite number `> 0`; `30` |
| `network.connect_timeout_seconds`, `read_timeout_seconds`, `write_timeout_seconds`, `pool_timeout_seconds` | `null` or finite number `> 0`; `null` inherits request timeout |
| `network.max_attempts` | strict integer `>= 1`; `3`, including initial request; `1` disables retry |
| `network.retry_max_delay_seconds` | finite number `>= 0`; `30`; jitter never exceeds it, `0` has no wait |
| `network.auth_refresh_attempts` | strict integer `>= 0`; `1` additional auth refresh attempt |
| `network.global_request_interval_seconds` | finite number `>= 0`; `0` |
| `network.pool_max_connections` | strict integer `>= 1`; `8` |
| `network.pool_max_idle_connections` | strict integer `0..pool_max_connections`; `8` |
| `network.max_response_bytes` | strict integer `>= 1`; `67108864`; checks declared and received sizes |
| `network.http2`, `follow_redirects` | strict boolean; both `true` |
| `network.proxy` | `null` or string; `null` |
| `network.headers` | string-to-string mapping; default User-Agent; display redacts values |
| `notification.enabled` | strict boolean; `false` |
| `notification.methods` | `desktop|email` list; `[desktop]` |
| `notification.notify_on` | `fetch_error|process_error|save_error|auth_error|config_error|plugin_error|update_error|storage_error|runtime_error` list |
| `notification.routes` | category to `desktop|email` list mapping; `{}` |
| `notification.desktop` | mapping; `{}` |
| `notification.email.smtp_host`, `from`, `username` | string; `''` |
| `notification.email.smtp_port` | strict integer `1..65535`; `465` |
| `notification.email.use_tls` | strict boolean; `true` |
| `notification.email.to` | string list; `[]` |
| `notification.email.credential_service` | string; `image-downloader.smtp` |
| `security.plugin_verification` | `strict|warn|off`; `strict`; only main/profile application layer |
| `image_processors.chain` | unique reverse-DNS processor ID list; `[]`; order is execution order |
| `plugin_settings.<id>.enabled` | strict boolean; `true` |
| `plugin_settings.<id>.config` | mapping; `{}`; plugin author validates it |
| `plugin_settings.<id>.secrets` | `lower_snake_case -> UPPERCASE_REFERENCE` mapping; `{}` |
| `fallback.generic_html.enabled` | strict boolean; `true` |

## Plugin settings and secrets

<a id="config-plugin-settings"></a>

Plugin ID must exist after discovery. Author defaults, persistent `config`, and per-operation `--plugin-config` merge deeply in that order. Disabled site plugins are selection candidatesではない。chain にある disabled processor は error ではなく skip され doctor/debug diagnostics に出る。processor に `secrets` を置くのは error である。raw credential を YAML, manifest, catalog, or plugin source に書かず、secret reference を environment/keyring provider に解決させる。

```yaml
image_processors:
  chain:
    - local.image-downloader.artifact-history-processor
    - local.image-downloader.resize-processor
plugin_settings:
  local.image-downloader.artifact-history-processor:
    config: {label: processed}
  local.image-downloader.resize-processor:
    config: {max_width: 1600, max_height: 1600}
```

## Compatibility migration

<a id="config-migration"></a>

| legacy key | v3 replacement |
| --- | --- |
| `network.max_retries` | `network.max_attempts` |
| `network.max_retry_wait_seconds` | `network.retry_max_delay_seconds` |
| `network.max_auth_retries` | `network.auth_refresh_attempts` |
| `network.max_concurrency` | `network.request_concurrency` |
| `network.host_max_concurrency` | `network.origin_request_concurrency` |
| `network.site_max_concurrency` | `network.registrable_domain_request_concurrency` |
| `network.request_interval_seconds` | `network.global_request_interval_seconds` |
| `network.max_connections`, `max_keepalive_connections` | `network.pool_max_connections`, `network.pool_max_idle_connections` |
| `network.max_chapter_concurrency` | `download.chapter_concurrency` |
| `continue_on_error`, `allow_empty_manifest` | `download.continue_on_image_error`, `download.allow_empty_chapter_manifest` |

v1/v2 plugin tree、`plugins.<id>` configuration、`plugin_catalog`、旧 config path は v3 が互換読込しない。明示的に新しい tree/schema へ移し、`config explain` と `doctor --json` を確認してから旧 data を廃棄する。
