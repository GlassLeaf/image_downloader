# Plugin hook reference

<a id="plugin-hooks"></a>

この文書は plugin hook の input/output、capability、例外の正本である。呼出順・回数・並行性は [execution lifecycle](../explanation/execution-lifecycle.md) を使用する。

<a id="plugin-selection"></a>

selection は enabled site unit の `matches_with_config()`（ある場合）と `matches()` を評価し、highest `selection_priority` を選ぶ。同じ最高 priority の複数 candidate は曖昧さとして `PluginError` にする。`matches_with_config` は private config にだけ依存でき、network/secret/filesystem access はしない。

| hook | signature | contract |
| --- | --- | --- |
| validation | `validate_config(self, config: Mapping[str, object], app_settings: Mapping[str, object]) -> None` | synchronous、side-effect free。invalid private config は `ValueError`。 |
| optional selection | `matches_with_config(self, url: str, config: Mapping[str, object], app_settings: Mapping[str, object]) -> bool` | synchronous、side-effect free、invalid private config に耐える。request/secret/filesystem を使わない。 |
| selection | `matches(self, url: str) -> bool` | synchronous bool。exception/non-bool は mismatch でなく `PluginError`。 |
| inspection | `async inspect(self, url: str, context: PluginExecutionContext) -> DownloadManifest` | operation ごとに一回、有限で完全な manifest。pagination はここで完結し、JS/DOM/browser execution はない。 |
| image request | `async create_image_request(self, image: ImageResource, context: PluginExecutionContext) -> RequestSpec` | image fetch 直前に一回。canonical image URL から header/referer/直前 API 解決を行う。 |
| recovery | `async recover_image_request(self, image: ImageResource, failed: RequestSpec, response: RequestResponse, context: PluginExecutionContext) -> RequestSpec | None` | AuthFlow refresh 後にも HTTP `status >=400` が残るときだけ最大一回。transport failure には呼ばれない。short-lived URL を再発行するか `None`。 |
| authentication | `auth_flow(self, context: PluginExecutionContext) -> AuthFlow | None` | `is_auth_failure(request: RequestSpec, response: RequestResponse) -> bool`、async `apply(request: RequestSpec) -> RequestSpec`、async `refresh(failed: RequestSpec, response: RequestResponse) -> RequestSpec | None` を実装する。secret 欠落は `SecretNotFound`、login failure は `AuthenticationError`。 |
| site transform | `async transform_image(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact` | network/secret capability なし。 |
| update | `async check_updates(self, url: str, context: PluginExecutionContext) -> UpdateSnapshot` | optional。partial delta でなく complete snapshot。 |
| processor transform | `async transform(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact` | configured chain の順に実行する。 |
| processor cleanup | `async aclose() -> None` または `close() -> None` | 両方なら `aclose` を優先。success/failure/cancellation/partial construction failure 後も cleanup される。 |

<a id="plugin-contexts"></a>

## Capability contexts

All mappings in the following table are read-only/frozen snapshots. A plugin
must return a new DTO rather than mutate a context object.

| context property | type | availability and capability |
| --- | --- | --- |
| `PluginExecutionContext.config` | `Mapping[str, object]` | selected site plugin's effective private config |
| `.app_settings` | `Mapping[str, object]` | redacted application settings; network headers/proxy, security policy, notification, credentials, and other plugin settings are excluded |
| `.manifest` | `Mapping[str, object]` | selected plugin manifest snapshot |
| `.catalog` | `Mapping[str, object] | None` | selected plugin catalog pin, or `None` for an unpinned/builtin unit |
| `.secrets` | `SecretProvider` | `get(name: str) -> str`; missing/unavailable is `SecretNotFound` |
| `.requests` | `RequestPort` | `await execute(RequestSpec) -> RequestResponse`; this is the only supplied network capability |
| `TransformContext.image_id` | `str | None` | source `ImageResource.image_id` |
| `.index` | `int` | source image index |
| `.config`, `.app_settings`, `.plugin_manifest`, `.catalog` | same mapping types as above | processor/site-transform metadata for the current unit |
| `.site_manifest`, `.site_catalog` | `Mapping[str, object] | None` | selected site metadata; nullable when no separate site metadata is available |
| `.manifest` | `DownloadManifest` | the inspected operation manifest |
| `.chapter` | `Chapter` | the chapter containing the image |

`TransformContext` deliberately has no request or secret provider.  A processor
that needs network or a credential is outside the processor contract; put that
work in the site request/transform stage instead.

`AuthFlow` は既定で operation URL の origin にだけ credential を送る。credentialed CDN は `OriginScopedAuthFlow.allowed_origins` に absolute origin を明示して opt-in する。environment、keyring、YAML へ refreshed token を永続書込みする API はない。

401 and 403 are always authentication failures before a custom
`AuthFlow.is_auth_failure()` predicate is consulted. For another status the
predicate can opt in to authentication handling. The request sequence is:
`apply`, transport, optional refresh (up to `network.auth_refresh_attempts`),
`apply` again, then `recover_image_request` at most once only for a remaining
HTTP status `>=400`. A refresh which returns `None`, no auth flow for an auth
response, or exhausted refresh attempts raises `AuthenticationError`; recovery
does not receive a transport exception.

invoker は manifest、chapter/image index、plugin-returned `ImageResource.url` と `RequestSpec.url` の absolute HTTP(S) **構文**、string mapping、hook return class、nested DTO、cleanup return を検証する。`ImageResource` constructor 自体は URL を検査しないため、syntactically valid な dummy URL を返さないことは plugin author の責務である。予期しない hook exception と contract violation は plugin ID と hook 名を持つ `PluginError` に変換し、`asyncio.CancelledError` は再送出する。core fetch/process/save failure を plugin 内で握りつぶしてはならない。

<a id="plugin-hook-errors"></a>

## Hook exception and result matrix

The invoker does not turn every exception into the same outcome. “Public
application error” below means an `ImageDownloaderError` subclass deliberately
raised by the plugin; it is propagated unchanged. An ordinary `Exception` is
wrapped as `PluginError` with the plugin ID and hook name. `asyncio.CancelledError`
is never converted and is re-raised so cancellation reaches the caller.

| hook group | allowed successful return | author-side validation/error | invoker result and operation effect |
| --- | --- | --- | --- |
| `validate_config`, `matches_with_config`, `matches` | `None`, `bool`, `bool` | use `ValueError` for invalid private config in `validate_config`; match hooks must not use network/secrets/filesystem | invalid return/ordinary exception becomes `PluginError`; validation/selection fails the operation or diagnostic rather than silently treating it as a mismatch |
| `inspect`, `check_updates` | complete `DownloadManifest`, complete `UpdateSnapshot` | no partial pagination/delta; nested DTO/HTTP URL fields must be valid | invalid return/ordinary exception becomes `PluginError`; no result is returned for that operation |
| `auth_flow`, `is_auth_failure`, `apply`, `refresh` | `AuthFlow|None`, `bool`, `RequestSpec`, `RequestSpec|None` | secret absence: `SecretNotFound`; login/refresh refusal: `AuthenticationError` | invalid return/ordinary exception becomes `PluginError`; declared authentication failure ends the operation, not an image outcome |
| `create_image_request`, `recover_image_request` | `RequestSpec`, `RequestSpec|None` | return valid absolute HTTP(S) request DTOs | invalid return/ordinary exception becomes `PluginError`; request/auth/status failures follow the lifecycle and can become a per-image fetch outcome only when normal continuation applies |
| `transform_image`, processor `transform` | `ImageArtifact` | preserve valid bytes/content type/source URL; processor has no secret/network context | invalid return/ordinary exception becomes `PluginError`; normal image processing failures can be recorded as an image outcome when `continue_on_image_error=true`, whereas `PluginError` remains operation-level |
| `aclose` / `close` | `None` | cleanup must not return a value; `aclose` wins if both exist | ordinary exception/invalid return becomes `PluginError`; cleanup still runs on success, failure, cancellation, and partial construction paths |

The result boundary is defined in [execution lifecycle](../explanation/execution-lifecycle.md#result-boundary): callers find image-level failures through `ImageOutcome.failure`, not by catching a flattened plugin exception.

## Examples

<a id="plugin-examples"></a>

| need | source |
| --- | --- |
| relative image URL / Referer | [generic CSS selector](../../../plugin-sources/generic-css-selector/plugin.py) |
| HTML Origin/Referer | [public gallery](../../../plugin-sources/public-gallery/public_gallery.py) |
| cursor pagination and token | [cursor API gallery](../../../plugin-sources/cursor-api-gallery/cursor_api_gallery.py) |
| CSRF login | [CSRF login gallery](../../../plugin-sources/csrf-login-gallery/csrf_login_gallery.py) |
| OAuth refresh | [OAuth media API](../../../plugin-sources/oauth-media-api/oauth_media_api.py) |
| signed URL reissue | [signed CDN gallery](../../../plugin-sources/signed-cdn-gallery/signed_cdn_gallery.py) |
| update provider | [chaptered catalog](../../../plugin-sources/chaptered-catalog/chaptered_catalog.py) |
| processor contract | [artifact history processor](../../../plugin-sources/artifact-history-processor/artifact_history_processor.py) |
| Pillow resize | [resize processor](../../../plugin-sources/resize-processor/resize_processor.py) |

HTTP(S) 以外、SSE/WebSocket、JavaScript/DOM/headless browser、CAPTCHA、interactive MFA/WebAuthn、multipart/streaming body は contract 外である。必要な site は `UnsupportedSiteFeature` または明示的な plugin/authentication error で失敗させる。
