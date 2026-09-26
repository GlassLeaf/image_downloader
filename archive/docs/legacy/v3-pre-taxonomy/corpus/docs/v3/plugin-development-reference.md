# Plugin 開発参照

<a id="plugin-package"></a>

この文書は API v3 plugin package、manifest/trust、hook contract、テスト手順の正本である。hook の時系列・回数・失敗分岐は重複させず [実行ライフサイクル](execution-lifecycle.md) を参照する。`examples/plugin-v3-template` は layout の skeleton だけで、cursor pagination や短命 URL 解決を完成させた例ではない。

## Unit layout and metadata

<a id="plugin-layout"></a>

site unit は installed root の `site_plugins/<unit>/`、processor unit は `image_processor_plugins/<unit>/` に置く。source unit は次を直接含む。

```text
unit/
  manifest.json             # signer の生成物、runtime が読む
  plugin-metadata.json      # signer 入力、runtime は読まない
  plugin.yaml               # author default: root key は config だけ
  entry_module.py           # entry + relative helper modules
```

`plugin-metadata.json` は次の exact object である: `id` (publisher prefix を持つ reverse-DNS)、`publisher` (lowercase reverse-DNS)、`version` (PEP 440)、`kind` (`site_plugin|image_processor_plugin`)、`capabilities` (opaque `string[]`)、`match_priority` (integer)、`entry` (`{"file":"relative/path.py","class":"ClassName"}`)、`config_file` (relative lowercase `.yaml`)。capabilities は権限、dependency、実行制御、catalog pin ではない。processor の match priority は予約 metadata である。

<a id="plugin-manifest"></a>

strict `manifest.json` wrapper は exact keys `manifest` と `signature` を持つ。inner manifest は metadata fields に加え、`schema_version: 1`、`api_version: "3"`、Base64 raw 32-byte Ed25519 `public_key`、public-key SHA-256 `key_id`、`file_tree`、canonical file-tree SHA-256 `file_tree_sha256` を持つ。unknown/missing field は strict validation failure である。ID は publisher + `.` で始まり、entry/config path は POSIX relative path で plugin directory 外へ出られない。

`file_tree` は `manifest.json` を除く全 regular source file の relative `/` path から lower-case SHA-256 hex への mapping である。link、Windows reparse point、non-regular file は許可されない。`__pycache__/*.pyc` だけは除外されるが、cache 内の他 file は対象である。helper、metadata、author YAML を変えたら tree/hash/signature を再生成する。install は cache directory と `.pyc` を copy しない。

## Signing, catalog, and verification

<a id="plugin-signing"></a>

Bundled `tools/sign_local_site_plugin.py` は名前に反して site plugin と image processor plugin の両方を、metadata の `kind` に従って署名できる。

```powershell
python tools/sign_local_site_plugin.py --key path/to/local.ed25519.pem `
  plugin-sources/artifact-history-processor plugin-sources/resize-processor
```

署名済み source を `plugin install` すると staged copy、tree/signature/class contract validation、atomic placement、catalog trust を順に行う。`trust` は catalog entry を作成/更新、`revoke` は entry を残して `revoked: true`、`uninstall` は installation と entry を削除する。source directory 名でなく manifest ID が identity である。同 version で tree/content を変える更新、kind/publisher の変更は拒否される。version を進めた key rotation、downgrade、revoked ID 再 trust、priority 変更は管理者確認が必要である。

<a id="plugin-catalog"></a>

normal catalog pin は ID/kind/publisher/version/public key/key id/manifest digest/file-tree digest/selection priority/revoked を pin する。content pin variant は `id, kind, manifest_digest, content_digest, selection_priority, revoked` を pin し、旧 key fields を含まない。verification の意味は次のとおり。

| mode | behavior |
| --- | --- |
| `strict` | catalog と complete manifest/tree/signature が必要。問題の unit は failed/skip diagnostic。 |
| `warn` | structural validation の後、catalog/individual verification 問題を warning として unpinned load を試みる。 |
| `off` | `bypass-all` 相当。catalog と signature/tree を迂回し minimum manifest を許す。 |
| `bypass-all` | catalog、signature/tree を迂回。 |
| `bypass-catalog` | catalog だけを迂回し complete source signature/tree は必須。 |
| `bypass-signature` | content-pinned catalog と actual content digest を必須にし、signature だけを迂回。 |

弱い mode は開発/復旧に限定し、production config の `strict` を置換しない。

## Selection and configuration

<a id="plugin-selection"></a>

enabled external site plugin だけが候補になる。`matches_with_config(url, config, app_settings)` があればまず使い、なければ `matches(url)` を使う。最高の manifest `match_priority`、次に catalog `selection_priority` で一つを選ぶ。両方同順位なら `PluginError("plugin priority conflict for URL")` であり任意選択しない。candidate がなければ fallback が enabled のときだけ `core.generic-html` を使う。invalid/non-boolean match return や hook exception は不一致ではなく PluginError になる。

author default `plugin.yaml`、user `plugin_settings.<id>.config`、operation override は deep merge される。`validate_config` は each selected site/enabled chain processor に対し同期・副作用なしで呼ばれ、`None` を返す。unknown runtime override、未選択 site/processor ID、processor secret、duplicate chain は configuration failure である。

## Hook contracts

<a id="plugin-hooks"></a>

| hook | signature and contract |
| --- | --- |
| validation | `validate_config(config: Mapping[str, object], app_settings: Mapping[str, object]) -> None`; synchronous, side-effect free. Invalid private config raises `ValueError`. |
| optional selection | `matches_with_config(url, config, app_settings) -> bool`; synchronous, side-effect free, tolerant of invalid private config; no request/secret/filesystem capability. |
| selection | `matches(url: str) -> bool`; synchronous. |
| inspection | `async inspect(url: str, context: PluginExecutionContext) -> DownloadManifest`; one finite deterministic manifest. Do all cursor/page API traversal here; no JavaScript/DOM/browser execution exists. |
| image request | `async create_image_request(image: ImageResource, context: PluginExecutionContext) -> RequestSpec`; once per image and may run concurrently. `ImageResource.url` was already an absolute HTTP(S) URL, never a placeholder. Use this for just-in-time resolver API. |
| recovery | `async recover_image_request(image, failed, response, context) -> RequestSpec | None`; only response-backed image failure, at most once. Reissue a signed URL or return `None` for normal image failure flow. |
| authentication | `auth_flow(context) -> AuthFlow | None`; `is_auth_failure`, async `apply`, async `refresh` must satisfy `AuthFlow`. Missing secret uses `SecretNotFound`; login failure uses `AuthenticationError`. |
| site transform | `async transform_image(artifact: ImageArtifact, context: TransformContext) -> ImageArtifact`; no request or secret capability. |
| updates (optional) | `async check_updates(url, context) -> UpdateSnapshot`; return a complete snapshot, not a partial delta. |
| processor transform | `async transform(artifact, context) -> ImageArtifact`; each configured processor runs in `image_processors.chain` order. |
| processor cleanup (optional) | `close() -> None` or `async aclose() -> None`; return must be `None`. Runtime calls it while closing the processor lifecycle. |

The invoker validates manifest, chapter/image ordering and indexes, absolute HTTP(S) request/image URLs, RequestSpec semantics, hook return class, and cleanup result. Any plugin exception or invalid hook result is normalized to `PluginError`; do not catch expected core fetch/process/save failures inside a plugin merely to hide them. `asyncio.CancelledError` is propagated.

`PluginExecutionContext` exposes only immutable `config`, redacted `app_settings`, manifest/catalog metadata, `secrets`, and `requests`. `TransformContext` exposes immutable config/metadata plus image id/index, manifest and chapter but deliberately has no network or secret provider. `AuthFlow` normally applies credentials only to the operation origin; `OriginScopedAuthFlow.allowed_origins` is an explicit opt-in for a credentialed CDN.

## Supported patterns and examples

<a id="plugin-examples"></a>

| need | complete source |
| --- | --- |
| relative image URL / Referer | [generic CSS selector](../../plugin-sources/generic-css-selector/plugin.py) |
| HTML Origin/Referer | [public gallery](../../plugin-sources/public-gallery/public_gallery.py) |
| cursor API pagination and token | [cursor API gallery](../../plugin-sources/cursor-api-gallery/cursor_api_gallery.py) |
| CSRF login | [CSRF login gallery](../../plugin-sources/csrf-login-gallery/csrf_login_gallery.py) |
| OAuth refresh | [OAuth media API](../../plugin-sources/oauth-media-api/oauth_media_api.py) |
| short-lived URL reissue | [signed CDN gallery](../../plugin-sources/signed-cdn-gallery/signed_cdn_gallery.py) |
| chapter manifest and update provider | [chaptered catalog](../../plugin-sources/chaptered-catalog/chaptered_catalog.py) |
| smallest processor contract | [artifact history processor](../../plugin-sources/artifact-history-processor/artifact_history_processor.py) |
| Pillow resize to PNG | [resize processor](../../plugin-sources/resize-processor/resize_processor.py) |

No API provides JavaScript execution, DOM automation/headless browser, WebSocket/SSE, multipart/streaming body, CAPTCHA, interactive MFA, or WebAuthn. A site that requires them should fail as `UnsupportedSiteFeature` or a documented plugin/authentication error; do not emulate browser credentials with unrestricted filesystem/process access.

## Plugin test checklist

<a id="plugin-tests"></a>

1. Sign the exact source tree and verify strict `read_manifest`/`verify_signed_plugin_source`.
2. Test valid and malformed manifest, catalog, source response, config, match result, request spec, and hook return value.
3. Use a fake `RequestPort` to test pagination, auth refresh, and signed URL recovery; assert no direct network/filesystem access is required.
4. Assert selection priority/tie/fallback, disabled plugin behavior, transform order, cancellation, and optional cleanup.
5. Install into a temporary root and run `doctor --json`; test staged rollback on invalid class/config.

Repository contract tests cover all these boundaries. Plugin authors should add equivalent unit tests before publishing a signed source.
