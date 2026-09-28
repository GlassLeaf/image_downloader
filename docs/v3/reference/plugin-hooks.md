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
| image request | `async create_image_request(self, image: ImageResource, context: PluginExecutionContext) -> RequestSpec | ImageFetchRequest` | image fetch 直前に一回。`image.url` の locator（canonical URL、ID、placeholder のいずれでもよい）と `image_id` から header/referer/直前 API 解決を行い、実際に送信する absolute HTTP(S) `RequestSpec.url` を返す。request-time の transform data が必要なら `ImageFetchRequest(request, plugin_data)` を返せる。locator 自体は transport に送られない。 |
| recovery | `async recover_image_request(self, image: ImageResource, failed: RequestSpec, response: RequestResponse, context: PluginExecutionContext) -> RequestSpec | ImageFetchRequest | None` | AuthFlow refresh 後にも HTTP `status >=400` が残るときだけ最大一回。transport failure には呼ばれない。short-lived URL を再発行するか `None`。bare `RequestSpec` は直前の `plugin_data` を維持し、`ImageFetchRequest` はそれを置換する。 |
| authentication | `auth_flow(self, context: PluginExecutionContext) -> AuthFlow | None` | `is_auth_failure(request: RequestSpec, response: RequestResponse) -> bool`、async `apply(request: RequestSpec) -> RequestSpec`、async `refresh(failed: RequestSpec, response: RequestResponse) -> RequestSpec | None` を実装する。secret 欠落は `SecretNotFound`、login failure は `AuthenticationError`。 |
| site transform | `async transform_image(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact` | network/secret capability なし。 |
| update | `async check_updates(self, url: str, context: PluginExecutionContext) -> UpdateSnapshot` | optional。partial delta でなく complete snapshot。 |
| processor transform | `async transform(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact` | configured chain の順に実行する。 |
| optional cleanup after runtime use | `cleanup_after_use() -> Awaitable[None] | None` | selected site instance と configured processor instance に任意で実装する。runtime がその instance の利用を終えると一回だけ呼び、awaitable は await する。最終戻り値は `None`。 |

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
| `.image_metadata` | `Mapping[str, str]` | source `ImageResource.metadata` の immutable、非秘密な inspection-time metadata |
| `.transport_metadata` | `ImageTransportMetadata | None` | successful image fetch の transform 専用 snapshot。site transform は raw 値を受ける。processor は `image_processors.transport_metadata_access` で selected site ID を明示許可したときだけ raw 値、それ以外は `is_redacted=True` の同型 snapshot を受ける。 |
| `.config`, `.app_settings`, `.plugin_manifest`, `.catalog` | same mapping types as above | processor/site-transform metadata for the current unit |
| `.site_manifest`, `.site_catalog` | `Mapping[str, object] | None` | selected site metadata; nullable when no separate site metadata is available |
| `.manifest` | `DownloadManifest` | the inspected operation manifest |
| `.chapter` | `Chapter` | the chapter containing the image |

`TransformContext` deliberately has no request or secret provider. A processor
that needs network or a credential is outside the processor contract; put that
work in the site request stage instead. `transport_metadata` は network capability
ではなく、成功した画像 fetch の限定 snapshot である。raw URL query、header/cookie 値、`ImageFetchRequest.plugin_data` は artifact、result、event、log、CLI JSON へ自動コピーされない。processor へ raw 値を渡すときは、ユーザーが processor/site の組を永続設定で明示許可する。

`AuthFlow` は既定で operation URL の origin にだけ credential を送る。credentialed CDN は `OriginScopedAuthFlow.allowed_origins` に absolute origin を明示して opt-in する。environment、keyring、YAML へ refreshed token を永続書込みする API はない。

401 and 403 are always authentication failures before a custom
`AuthFlow.is_auth_failure()` predicate is consulted. For another status the
predicate can opt in to authentication handling. The request sequence is:
`apply`, transport, optional refresh (up to `network.auth_refresh_attempts`),
`apply` again, then `recover_image_request` at most once only for a remaining
HTTP status `>=400`. A refresh which returns `None`, no auth flow for an auth
response, or exhausted refresh attempts raises `AuthenticationError`; recovery
does not receive a transport exception.

invoker は manifest、chapter/image index、plugin-returned `ImageResource.url` と `ImageArtifact.source_url` の **non-empty string locator**、`ImageResource.metadata` と `ImageFetchRequest.plugin_data` の string mapping、`RequestSpec.url` と referer の absolute HTTP(S) **構文**、hook return class、nested DTO、cleanup return を検証する。`ImageResource` constructor 自体は URL を検査しない。locator は plugin が `create_image_request` で実 request を組み立てるのに十分に安定していなければならない。`image_id`、image metadata、manifest metadata は inspection-time の非秘密値だけにし、token、cookie、署名 URL、鍵などを含めてはならない。`plugin_data` は request-time の transform 専用値であり、必要最小限にして artifact/result/event/log へ自分でコピーしてはならない。core は locator の意味を解釈せず、`RequestSpec(url=image.url)` が HTTP(S) でなければ fetch 前に失敗する。予期しない hook exception と contract violation は plugin ID と hook 名を持つ `PluginError` に変換し、`asyncio.CancelledError` は再送出する。core fetch/process/save failure を plugin 内で握りつぶしてはならない。

```python
async def create_image_request(self, image, context):
    signed = await context.requests.execute(RequestSpec(f"https://api.example.test/sign/{image.image_id}"))
    return ImageFetchRequest(
        RequestSpec(signed.headers["x-image-url"]),
        {"variant": image.metadata["variant"]},
    )
```

<a id="plugin-hook-errors"></a>

## Hook exception and result matrix

The invoker does not turn every exception into the same outcome. “Public
application error” below means an `ImageDownloaderError` subclass deliberately
raised by the plugin; it is propagated unchanged except from
`cleanup_after_use`, whose exception is always reported as `PluginError`. An
ordinary `Exception` is wrapped as `PluginError` with the plugin ID and hook
name. `asyncio.CancelledError` is never converted and is re-raised so
cancellation reaches the caller.

| hook group | allowed successful return | author-side validation/error | invoker result and operation effect |
| --- | --- | --- | --- |
| `validate_config`, `matches_with_config`, `matches` | `None`, `bool`, `bool` | use `ValueError` for invalid private config in `validate_config`; match hooks must not use network/secrets/filesystem | invalid return/ordinary exception becomes `PluginError`; validation/selection fails the operation or diagnostic rather than silently treating it as a mismatch |
| `inspect`, `check_updates` | complete `DownloadManifest`, complete `UpdateSnapshot` | no partial pagination/delta; image/source locators must be non-empty, request/update URL fields must be HTTP(S) | invalid return/ordinary exception becomes `PluginError`; no result is returned for that operation |
| `auth_flow`, `is_auth_failure`, `apply`, `refresh` | `AuthFlow|None`, `bool`, `RequestSpec`, `RequestSpec|None` | secret absence: `SecretNotFound`; login/refresh refusal: `AuthenticationError` | invalid return/ordinary exception becomes `PluginError`; declared authentication failure ends the operation, not an image outcome |
| `create_image_request`, `recover_image_request` | `RequestSpec|ImageFetchRequest`, `RequestSpec|ImageFetchRequest|None` | return valid absolute HTTP(S) request DTOs; plugin data is immutable string mapping | invalid return/ordinary exception becomes `PluginError`; request/auth/status failures follow the lifecycle and can become a per-image fetch outcome only when normal continuation applies |
| `transform_image`, processor `transform` | `ImageArtifact` | preserve valid bytes/content type/source locator; processor has no secret/network context | invalid return/ordinary exception becomes `PluginError`; normal image processing failures can be recorded as an image outcome when `continue_on_image_error=true`, whereas `PluginError` remains operation-level |
| `cleanup_after_use` | `None` または await 後に `None` | selected site/processor instance の runtime 利用後の任意 cleanup。属性がある場合は callable でなければならない。runtime はこの hook の完了後、同じ instance の plugin hook を再度呼ばない。temporary matcher instance、`aclose`、`close`、`aclose_operation`、`close_operation` は対象外 | normal completion では exception/invalid return が `PluginError`。operation failure/cancellation を置換せず diagnostic に残る。processor の partial construction failure 後も実行する |

The result boundary is defined in [execution lifecycle](../explanation/execution-lifecycle.md#result-boundary): callers find image-level failures through `ImageOutcome.failure`, not by catching a flattened plugin exception.

## Cleanup after runtime use

`cleanup_after_use()` は object 自体を close/dispose する hook ではなく、runtime が一つの instance を使い終えた後の後始末用である。site plugin は selected instance にだけ `run()` または `check_updates()` の終了時に、processor は configured instance に download の終了時に呼ばれる。processor cleanup は site cleanup より先である。通常終了時の failure は `PluginError`、すでに operation failure または cancellation があるときは主原因を維持して diagnostic に残す。

```python
class GalleryPlugin:
    async def cleanup_after_use(self) -> None:
        await self._temporary_session.aclose()


class WatermarkProcessor:
    def cleanup_after_use(self) -> None:
        self._scratch_paths.clear()
```

この hook が終わった後、runtime はその instance の plugin hook を再び呼ばない。ただし、Python object の破棄や外部コードによる呼出しを禁止するものではない。selection 用だけの temporary matcher instance には hook を呼ばないため、constructor と `matches()` は cleanup が必要な外部 resource を確保してはならない。以前の `aclose()`、`close()`、`aclose_operation()`、`close_operation()` は cleanup hook として呼ばれない。既存 plugin は `cleanup_after_use()` へ移行する。

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
