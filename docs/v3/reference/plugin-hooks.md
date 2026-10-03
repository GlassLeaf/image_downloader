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
| inspection | `async inspect(self, url: str, context: PluginExecutionContext) -> DownloadManifest` | operation ごとに一回、有限で完全な manifest。pagination はここで完結し、JS/DOM/browser execution はない。画像の論理的な元名が分かる site plugin は `ImageResource.original_filename` に非秘密な単一ファイル名を設定できる。 |
| image request | `async create_image_request(self, image: ImageResource, context: PluginExecutionContext) -> RequestSpec | ImageFetchRequest` | image fetch 直前に一回。`image.url` の locator（canonical URL、ID、placeholder のいずれでもよい）と `image_id` から header/referer/直前 API 解決を行い、実際に送信する absolute HTTP(S) `RequestSpec.url` を返す。request-time の transform data が必要なら `ImageFetchRequest(request, plugin_data)` を返せる。locator 自体は transport に送られない。 |
| recovery | `async recover_image_request(self, image: ImageResource, failed: RequestSpec, response: RequestResponse, context: PluginExecutionContext) -> RequestSpec | ImageFetchRequest | None` | AuthFlow refresh 後にも HTTP `status >=400` が残るときだけ最大一回。transport failure には呼ばれない。short-lived URL を再発行するか `None`。bare `RequestSpec` は直前の `plugin_data` を維持し、`ImageFetchRequest` はそれを置換する。 |
| authentication | `auth_flow(self, context: PluginExecutionContext) -> AuthFlow | None` | `is_auth_failure(request: RequestSpec, response: RequestResponse) -> bool`、async `apply(request: RequestSpec) -> RequestSpec`、async `refresh(failed: RequestSpec, response: RequestResponse) -> RequestSpec | None` を実装する。secret 欠落は `SecretNotFound`、login failure は `AuthenticationError`。display-only inspection でも送信直前 request を組み立てるため `apply()` は呼ばれる。 |
| site transform | `async transform_image(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact` | network/secret capability なし。 |
| update | `async check_updates(self, url: str, context: PluginExecutionContext) -> UpdateSnapshot` | optional。partial delta でなく complete snapshot。 |
| processor transform | `async transform(self, artifact: ImageArtifact, context: TransformContext) -> ImageArtifact` | configured chain の順に実行する。 |
| optional output format values | `output_format_values(self, context: PluginFormatContext) -> Mapping[str, str]` | selected site plugin と enabled processor に任意で実装できる同期 hook。operation 構成後に一度だけ呼ばれ、同一 operation 内では時点によらず同じ、大文字スネークケース key の非秘密 string mapping を返す。未実装は空 mapping。 |
| optional cleanup after runtime use | `cleanup_after_use() -> Awaitable[None] | None` | selected site instance と configured processor instance に任意で実装する。runtime がその instance の利用を終えると一回だけ呼び、awaitable は await する。最終戻り値は `None`。 |

更新確認hookがない場合は`UpdateCheckUnsupportedError(PluginError)`を返す。
codeは`update_check_unsupported`、安全な固定reasonは`selected plugin does not support update checks`。
hook内の通常の失敗や不正な返却値は引き続き`PluginError`として区別する。

## Optional additional files

選択されたsite pluginは任意に `AdditionalFileProvider` を実装できる。
必須 `SitePlugin` は変更しない。最初の2メソッドは対で実装し、callbackは個別に省略できる。

| method | signature / return |
| --- | --- |
| declaration | `additional_file_hook_points(self, context: PluginExecutionContext) -> tuple[AdditionalFileHookPoint, ...]` |
| provider | `async additional_files(self, hook: AdditionalFileHookContext, context: PluginExecutionContext) -> tuple[AdditionalFileSpec, ...]` |
| received callback | `async additional_file_received(self, result: AdditionalFileReceiveResult, context: PluginExecutionContext) -> None` |
| saved callback | `async additional_file_saved(self, result: AdditionalFileSaveResult, context: PluginExecutionContext) -> None` |

宣言は各download試行で一回、同期・副作用なし。結果を固定し、宣言順によらず以下の順で呼ぶ。
未知・重複段階、非tuple、片方だけの実装はdeclaration failureを記録し、その試行で無効化する。

| point | timing / available context |
| --- | --- |
| `BEFORE_MANIFEST` | inspect前に一回。manifest/chapter/imageはNone |
| `AFTER_MANIFEST` | manifest検証・章保存先決定後、章ごと。manifest/chapterあり |
| `BEFORE_IMAGE_REQUEST` | create_image_request前、対象画像ごと。imageもあり |
| `AFTER_IMAGE_REQUEST` | auth/recoveryを含む画像取得の最終成功後、変換前。responseもあり |
| `BEFORE_IMAGE_SAVE` | 変換後、画像保存先割り当て前。artifactもあり |
| `AFTER_IMAGE_SAVE` | 保存またはスキップ後。image_outcomeに確定パス・状態あり |
| `AFTER_DOWNLOAD` | 画像処理通常完了後、章ごと。chapter_resultあり。plugin cleanupより前 |

主処理失敗・キャンセルで未到達の段階は呼ばない。画像失敗を結果として返す通常完了では
AFTER_DOWNLOADを呼ぶが、後続cleanupで全体が失敗し得る。inspection・plan/dry-run・更新確認だけでは
宣言も追加処理も実行しない。

仕様は `AdditionalFileSpec(file_id, relative_path, request=...)` または `data=bytes`。
IDは `[A-Za-z0-9_.-]{1,128}` の非秘密識別子。relative_pathは章別画像フォルダ基準で、例えば
`metadata/source.json` をその章内に保存する。絶対パス・drive/UNC・親参照・dot/空成分・Windows不正名/ADS・
symlink/reparse pointを拒否する。既存画像を含む衝突は `output.existing_file` に従う。

返却順に取得→received callback→保存/スキップ→saved callbackをawaitしてから次へ進む。
通信は既存gatewayの認証origin・concurrency・size制限を使う。bytesと既存ファイル読み込みにも
`network.max_response_bytes` を適用する。通信・callback中は保存先ロックを保持しない。
原子的書き込みはファイル単位で、画像と追加ファイルの一括transactionはない。

received callbackは取得成功・失敗で一回。bytesも成功として扱いresponse=None。
失敗時はdata=Noneで保存せず、saved callbackも呼ばない。成功時は生bytesとAPIのresponseを渡す。
saved callbackはsaved/skipped/failedで一回。savedは書いた内容、skippedは**既存ファイルの内容**を渡す。
読み込み失敗・サイズ超過時は保存状態を維持し、data=Noneとread_errorを渡す。
callbackの例外・不正な戻り値は別の失敗として記録し、保存取り消し・主処理失敗にはしない。
received callback失敗でも保存を続ける。成功戻り値はNone。CancelledErrorは伝播する。

BEFORE_MANIFESTの取得・received callbackはinspect前に実行する。内容は専用一時領域に保持し、
manifest確定後、同じ内容を各章へ保存する。saved callbackには元pointと対象manifest/chapterを渡す。
章0件はsave/no_targetを記録する。一時領域は成功・失敗・キャンセルで後始末する。

生データは選択site専用で、公開結果・event・log・CLI JSON・historyへコピーしない。
callbackのpathは実保存先、公開pathは相対・秘匿済み。保存内容自体を自動redactionはしないため、
秘密情報を保存するかはpluginが明示的に判断する。必須データはpluginが保持し、後続画像hookで不足を
明示的に失敗させる。画像request/transformへの受け渡しには既存 `ImageFetchRequest.plugin_data` を使える。

並行callbackはoperation_id/attempt_number/invocation_idとchapter/imageで区別する。
保持画像には画像hook/callbackを再実行せず、対応結果を保持する。新試行は必要なデータを再取得する。
追加ファイルだけのretryはない。外部副作用の冪等性はplugin側で扱う。
`DownloadResult.additional_files` はreceive/save/read/callbackの安全な結果を別々に記録し、画像の
saved_files/failures・CLI終了コード・retry判定を変更しない。CLI/workflow履歴にも別項目で投影し、
旧履歴の欠落項目は空として読む。
例は [additional-files sample](../../../examples/plugin-v3-additional-files/README.md) を参照する。

## Manifest numbering

`inspect()` を実装する site plugin は、manifest の採番にも責任を持つ。複数の
`Chapter` を含む `DownloadManifest` では、各 `Chapter.number` を manifest 内で
重複しない値にし、1 始まりの連番を使用することが推奨される。各
`Chapter.images` でも `ImageResource.index` を重複しない値にし、1 始まりの連番を
使用することが推奨される。

core は plugin が返した number/index を再採番、正規化、重複修正しない。出力
template は `%CHAPTER_NUMBER%` と `%IMAGE_INDEX%` にその値を四桁ゼロ埋めで展開する。
番号を重複させてテンプレートが同じ出力名を作る場合、最終的な扱いは
`output.existing_file` の collision policy に従う。採番の詳細は
[configuration reference](configuration.md#config-storage-layout) を参照する。

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
| `PluginFormatContext.plugin_id`, `.plugin_kind`, `.operation_url` | `str` | output-format provider の実行中 plugin identity と operation URL。mapping は image/chapter/artifact ではなくこの operation-stable context だけに依存する。 |
| `.config`, `.app_settings`, `.manifest`, `.catalog` | same mapping types as above | output-format provider の current plugin config、redacted app settings、plugin manifest/catalog snapshot。request/secret/filesystem capability はない。 |
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

`output_format_values()` is optional and synchronous. Core calls it once after the selected site and enabled processor chain have been composed, then freezes the returned mapping for all output names in that operation. `PluginFormatContext` and all mappings it exposes are immutable. The hook must not perform I/O or depend on mutable time, image, artifact, `DownloadManifest`, or chapter state. It returns a mapping such as `{"FILTER_NAME": "_grayscale"}` from `com.example.grayscale`; core namespaces it as `%PLUGIN[com.example.grayscale:FILTER_NAME]%`. Values are non-secret output text. A missing hook, selected plugin, or key is not an error; the matching token remains literal before path-component safety conversion. Exceptions, non-mapping returns, non-string values, and keys outside `[A-Z][A-Z0-9_]{0,63}` are `PluginError`. A future plugin kind participates only when its operation composer deliberately registers it as an output-format value provider.

`AuthFlow` は既定で operation URL の origin にだけ credential を送る。credentialed CDN は `OriginScopedAuthFlow.allowed_origins` に absolute origin を明示して opt-in する。environment、keyring、YAML へ refreshed token を永続書込みする API はない。display-only inspection は `apply()` を呼ぶが image request を送らない。`apply()` が token 発行などの補助 HTTP や外部 state 変更を自ら行えば、そのサーバー側副作用は取り消せない。可能な限り `apply()` は request を決定するだけにし、必要な補助 HTTP は明示的に扱う。

401 and 403 are always authentication failures before a custom
`AuthFlow.is_auth_failure()` predicate is consulted. For another status the
predicate can opt in to authentication handling. The request sequence is:
`apply`, transport, optional refresh (up to `network.auth_refresh_attempts`),
`apply` again, then `recover_image_request` at most once only for a remaining
HTTP status `>=400`. A refresh which returns `None`, no auth flow for an auth
response, or exhausted refresh attempts raises `AuthenticationError`; recovery
does not receive a transport exception.

invoker は manifest、chapter/image index、plugin-returned `ImageResource.url` と `ImageArtifact.source_url` の **non-empty string locator**、`ImageResource.original_filename` の非空・path separator なし string、`ImageResource.metadata` と `ImageFetchRequest.plugin_data` の string mapping、`RequestSpec.url` と referer の absolute HTTP(S) **構文**、hook return class、nested DTO、cleanup return を検証する。`ImageResource` constructor 自体は URL を検査しない。locator は plugin が `create_image_request` で実 request を組み立てるのに十分に安定していなければならない。`image_id`、image metadata、manifest metadata、`original_filename` は inspection-time の非秘密値だけにし、token、cookie、署名 URL、鍵などを含めてはならない。`plugin_data` は request-time の transform 専用値であり、必要最小限にして artifact/result/event/log へ自分でコピーしてはならない。plugin は `ImageSaveOptions(format="ORIGINAL")` により core の再エンコードを止められる。この指定は extension/encoder field と併用できず、site transform/processor 後の artifact bytes を保存する。core は locator の意味を解釈せず、`RequestSpec(url=image.url)` が HTTP(S) でなければ fetch 前に失敗する。予期しない hook exception と contract violation は plugin ID と hook 名を持つ `PluginError` に変換し、`asyncio.CancelledError` は再送出する。core fetch/process/save failure を plugin 内で握りつぶしてはならない。

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
| `output_format_values` | `Mapping[str, str]` | optional, synchronous, stable non-secret mapping with `^[A-Z][A-Z0-9_]{0,63}$` keys; an absent hook is empty mapping | invalid return/ordinary exception becomes `PluginError`; values are collected before inspection and reused for the operation |
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
| username/password secret | [CSRF login gallery](../../../plugin-sources/csrf-login-gallery/csrf_login_gallery.py) |
| signed URL reissue | [signed CDN gallery](../../../plugin-sources/signed-cdn-gallery/signed_cdn_gallery.py) |
| update provider | [chaptered catalog](../../../plugin-sources/chaptered-catalog/chaptered_catalog.py) |
| processor contract | [artifact history processor](../../../plugin-sources/artifact-history-processor/artifact_history_processor.py) |
| Pillow resize | [resize processor](../../../plugin-sources/resize-processor/resize_processor.py) |
| image metadata in request/site transform and allow-listed processor handoff | [metadata bridge pair](../../../examples/plugin-v3-metadata-bridge/README.md) |

HTTP(S) 以外、SSE/WebSocket、JavaScript/DOM/headless browser、CAPTCHA、interactive MFA/WebAuthn、multipart/streaming body は contract 外である。必要な site は `UnsupportedSiteFeature` または明示的な plugin/authentication error で失敗させる。
