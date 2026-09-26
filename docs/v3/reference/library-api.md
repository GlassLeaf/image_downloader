# Library API reference

この文書、[configuration reference](configuration.md)、[CLI reference](cli.md)、[logging reference](logging.md)、[public signature index](api-signatures.md)、[API inventory](api-contract-inventory.md) が stable Python API の参照仕様である。inventory にある `__all__` 以外の import は stable ではない。async method は await する。`asyncio.CancelledError` は result に変換せず再送出する。

<a id="api-common"></a>
<a id="api-download-result"></a>

## Value objects, results, and protocols

| API | signature / meaning |
| --- | --- |
| `RequestSpec` | `(url: str, method="GET", headers=<fresh frozen mapping>, cookies=<fresh frozen mapping>, referer=None, query=<fresh frozen mapping>, form=<fresh frozen mapping>, json=None, auth_required=True, retry_non_idempotent=False)`。`form` と `json` の同時指定は `ValueError`。 |
| `RequestResponse` | `(url, status, headers, body)` immutable response。 |
| `ImageResource` | `(url, index=1, referer=None, headers=<fresh frozen mapping>, save_options=<fresh ImageSaveOptions>, image_id=None)`。constructor 自体は URL を検査しない immutable value object。hook invoker が plugin-returned image で absolute HTTP(S) の構文だけを検査する。意味上の dummy URL は runtime が識別できないため、plugin author の contract violation である。 |
| `ImageArtifact` | `(data, content_type, source_url, image_id=None, extension=None, history=())`。transform 入出力。 |
| `ImageSaveOptions` | `format, extension, quality, optimize, progressive, lossless, compress_level, exif=False`。`None` は config/default を使う。 |
| `Chapter` / `DownloadManifest` | immutable chapter/images と `(title, chapters, content_id=None, author=None, access=None, revision=None, metadata=<fresh frozen mapping>)`。 |
| `ImageFailure` | `kind, exception_type, message, code, reason, response_url, http_status, output_path, transport`。URL/章/index を持たない。 |
| `ImageOutcome` | `(image, kind, path=None, failure=None)`。failure と画像の相関はここから得る。 |
| `DownloadResult` | `(source_url, manifest, chapters)`。`saved_files`/`skipped_files` は absolute path string tuple、`failures` は相関を失う flatten tuple。 |
| outcome enums | `FailureKind` の member/value は `FETCH="fetch"`、`PROCESS="process"`、`SAVE="save"`、`ImageOutcomeKind` は `SAVED="saved"`、`SKIPPED="skipped"`、`FAILED="failed"`、`UpdateChangeKind` は `ADDED="added"`、`CHANGED="changed"`、`REMOVED="removed"`。member name と serialization の `.value` を混同しない。 |
| update DTO | `UpdateCandidate(url, content_id=None, revision=None)`、`UpdateSnapshot(source_url, candidates, checked_at)`、`UpdateChange(kind, url, content_id=None, revision=None)`、`UpdateResult(source_url, plugin_id, changes, checked_at)`。kind は ADDED/CHANGED/REMOVED。 |
| `RequestPort` | `async execute(spec: RequestSpec) -> RequestResponse`。`RequestError` family を送出し得る。 |
| `SecretProvider` | `get(name: str) -> str`。未設定/取得不能は `SecretNotFound`。 |
| `AuthFlow` | `is_auth_failure(request, response) -> bool`、`async apply(request) -> RequestSpec`、`async refresh(failed, response) -> RequestSpec | None`。 |
| `SitePlugin` / `ImageProcessor` / `UpdateProvider` | signature と hook failure は [plugin hook reference](plugin-hooks.md) が正本。 |

DTO は frozen で、JSON-like mapping は read-only mapping、sequence は tuple に freeze される。`<fresh …>` の default は dataclass default factory であり、mutable `{}` を共有する Python default ではない。変更は mutation でなく `dataclasses.replace()` または新しい DTO を使う。core は internal `freeze_json`/`thaw_json` で input と serialization payload を分離するが、それらの internal import は stable API ではない。serialization boundary は独立した mutable collection を生成し、caller collection と共有しない。

<a id="api-public-methods"></a>
<a id="api-configuration"></a>

## Runtime facade

| API | human-readable contract |
| --- | --- |
| `RuntimeComposer(config, *, config_root, plugin_root, plugin_verification_override=None)` | `compose() -> DownloadService` は log file、filesystem/state/cookie store、HTTP gateway、worker/image processor を作成してそれらを所有するため、compose 自体が filesystem/resource side effect を持つ。`compose_registry() -> PluginRuntime` は registry/module discovery だけを作り、gateway/logger/worker/file handle を作らず、caller が `close()` する。 |
| `DownloadService(config, registry, dependencies)` | `async run(url, *, plugin_overrides=None, fallback_override=None) -> DownloadResult`、`async check_updates(...) -> UpdateResult`、`async close() -> None`、`async with service`。operation は同一 service 内で直列、close は idempotent、close 後 run/update は `RuntimeError`。 |
| `RequestGateway(config, cookie_jar=None, *, logger=None)` | `operation(*, plugin_id, operation_url, auth_flow_factory=None, invoker=None) -> OperationRequestGateway`、`async execute(spec) -> RequestResponse`、`async close() -> None`。transport policy は [runtime behavior](runtime-behavior.md#runtime-transport)。 |
| `ArtifactPipeline(config, registry, site_record, overrides, image_processor, logger, invoker, processors)` | `async process(plugin, artifact, image, manifest, chapter) -> ImageArtifact`。site transform、validation、configured processor chain を実行する。 |
| `ChapterReporter(filesystem, chapter_directory, manifest, chapter, logger, reporter_id)` | `async start()`、`async record(position, outcome)`、`async finish(outcomes=None)`。chapter report/log を書く。 |
| `OutputAllocator(filesystem, config)` | `async refresh_directory(directory)`、`chapter_directory(chapter) -> Path`、`async allocate(chapter_directory, image, chapter, extension) -> OutputAllocation`。 |
| `OutputAllocation(relative_path, ...)` | `should_write` property、`async commit()`、`async abort()`。reservation を持つ `should_write=True` allocation は commit/abort のいずれか一回が必要で、二回目は `RuntimeError`。existing file による skip allocation (`should_write=False`) は reservation を持たず、`commit()`/`abort()` は完了操作不要の反復 no-op。 |
| `RuntimeSecrets(plugin_id, references)` | `get(name) -> str`。environment/keyring semantics は [configuration reference](configuration.md#config-secrets)。 |
| `UpdateState(filesystem, *, lock_timeout_seconds=30.0)` | `records() -> dict`、`save(records) -> None`、`apply_snapshot(plugin_id, source_url, snapshot) -> tuple[UpdateChange, ...]`、`async apply_snapshot_async(...)`。state contract は [runtime behavior](runtime-behavior.md#runtime-update-state)。 |
| `_RuntimeDependencies(outputs, logs, state, events, logger, notifications, cookie_store, cookie_baseline, gateway, image_processor, output_locks)` | stable re-export された advanced composition DTO。すべて compatible かつ open な dependency が必要で、通常は composer を使う。 |

service snapshot 後の plugin filesystem 変更は既存 service に再発見されない。HTTP pool、cookie jar、host/site limiter は service 単位で共有する一方、plugin ID、AuthFlow、allowed origin、refresh state は operation 単位で隔離する。`close()` は operation 完了を待ち、gateway/processor/logger を閉じ、cookie delta を保存する。

<a id="api-call-outcomes"></a>

## Caller outcomes, ownership, and cancellation

All runtime operations are asynchronous except composition and the explicitly
synchronous helpers shown in the signature index.  These are the boundaries a
library caller needs to handle; detailed Python signatures remain in
[api-signatures](api-signatures.md).

| API | return and ownership | side effect / concurrency | exceptions and caller action |
| --- | --- | --- | --- |
| `RuntimeComposer.compose()` | returns a resource-owning `DownloadService`; use `async with` or `await service.close()` | creates filesystem-backed logs/state/cookies, HTTP gateway, worker, and logger | configuration/notification/plugin initialization can raise before a service is returned. There is then no service object to close; correct the input or compose a fresh instance. Do not assume a partially built service is usable. |
| `RuntimeComposer.compose_registry()` | returns a `PluginRuntime`; **caller** must call its synchronous `close()` | performs plugin discovery/verification and isolated module loading only; it does not open runtime data stores, logger, gateway, or worker | `ConfigurationError` for invalid roots/settings and `PluginError` for source/verification/class failures. |
| `DownloadService.run()` | `DownloadResult`, whose normal image failures are correlated by `chapter.outcomes` | one operation per service at a time; plugin/config snapshot is fixed for that service | `AuthenticationError`, `ConfigurationError`, `PluginError`, `StorageSafetyError`, `InterProcessLockError`, fail-fast image error, and cancellation abort rather than return a partial result. Catch `ImageDownloaderError` only at an application boundary and use the concrete class/code for recovery. |
| `DownloadService.check_updates()` | complete `UpdateResult`; never a partial update result | serial with `run`; reads/compares/writes update state under its lock | `PluginError` if the selected site lacks `UpdateProvider`; `UpdateStateError`, auth/config/request/storage errors, or cancellation abort the operation. |
| `DownloadService.close()` / async context exit | `None`; idempotent | waits for current operation, persists cookie delta, closes gateway/processor/logger | after close, `run()` and `check_updates()` raise `RuntimeError`. Cleanup/observer failures are isolated from an already determined main result where documented in [runtime behavior](runtime-behavior.md#observability-and-notification). |
| `RequestGateway.execute()` | `RequestResponse` | transport uses the configured pool/retry/redirect/size policy | `RequestError` family for transport/status/policy/size conditions and `AuthenticationError` where auth is required. Use an operation gateway for plugin-authenticated work, not the root `execute()` method. |
| `OutputAllocator.allocate()` | `OutputAllocation` | reservations serialize competing paths inside the allocator; outer output lock coordinates processes | `ExistingFileConflictError` for `existing_file=error`, `OutputAllocationError` after exhausted rename candidates, `ConfigurationError` for an impossible component limit. Commit or abort a write reservation exactly once. |
| `UpdateState` methods | records or a complete tuple of `UpdateChange` | sync `apply_snapshot` is in-memory/file work; async form uses the inter-process update lock | `UpdateStateError`, `StorageSafetyError`, or `InterProcessLockError` mean no trustworthy update result; do not silently recreate a corrupt state file. |

`_RuntimeDependencies` is a stable advanced-composition DTO, not a default
factory. Its `outputs`, `logs`, `state`, `events`, `logger`, `notifications`,
`cookie_store`, `cookie_baseline`, `gateway`, `image_processor`, and
`output_locks` must be mutually compatible and open. Passing it to
`DownloadService` transfers their close lifecycle to that service. Its exact
constructor fields are frozen by the signature contract; normal callers should
use `RuntimeComposer` instead.

No public runtime API converts `asyncio.CancelledError` to `DownloadResult`,
`UpdateResult`, or an image failure. Let it propagate after any caller-specific
`finally` cleanup; calling `close()` remains the resource cleanup mechanism.

## Security facade

| API | signature / result |
| --- | --- |
| `CatalogEntry(...)` | `content_pinned` property と `as_json() -> dict`。normal/content-pin JSON shape は [plugin package reference](plugin-package.md#plugin-catalog)。 |
| `PluginCatalog(entries)` | `load(path) -> PluginCatalog`、`find(plugin_id) -> CatalogEntry|None`、`replace(entry) -> PluginCatalog`、`remove(plugin_id) -> PluginCatalog`。immutable replacement。 |
| `CatalogEntry` | `(id, kind, manifest_digest, selection_priority, revoked, publisher=None, version=None, public_key=None, key_id=None, file_tree_sha256=None, content_digest=None)`。`PluginKind` は `site_plugin|image_processor_plugin`。 |
| `PluginManifest(value, signature, digest, directory)` | verified manifest value object。value は frozen JSON mapping、signature は `str|None`、digest は manifest digest、directory は verified source root。 |
| discovery/verification DTO | `PluginDiagnostic(name, source, loaded, detail="", warning=False)`、`PluginDiscoveryResult(candidates, diagnostics)`、`PluginRecord(manifest, author_defaults, catalog, builtin=False)`、`PluginVerificationResult(records, catalog, diagnostics)`。`warning=True` は failed load と同義ではない。 |
| `PluginDiscovery(plugin_root, *, mode="strict")` | `discover() -> PluginDiscoveryResult`。 |
| `PluginManifestVerifier(plugin_root, *, mode)` | `verify(discovery) -> PluginVerificationResult`。 |
| `PluginClassLoader()` | `register_class`、`load_class`、`module_namespace`、`unload`、`close`、`construct`、`site_instance`、`processor_instance`、`typed_instance`。isolated module namespace を所有し、invalid source/type は `PluginError`。 |
| `PluginRegistry(records=(), *, catalog=None)` | `records` property、`get(plugin_id) -> PluginRecord|None`。immutable record snapshot。 |
| `PluginRuntime(config, plugin_root, *, mode)` | `register_builtin`、`validate_settings`、`enabled`、`effective_config`、override validation、instance/module access、`prepare`、`doctor_validate`、`validate_all`、`resolve`、`processor`、`close`。invalid config は `ConfigurationError`、source/type は `PluginError`。 |
| `PluginSelector(loader)` | `select(records, url, *, fallback_enabled, overrides, enabled, effective_config, app_settings)`。priority/tie behavior は [plugin hooks](plugin-hooks.md)。 |
| `catalog_entry_for` / `catalog_path` / `canonical_jcs` / `plugin_content_digest` | pure catalog/signature helpers。 |
| `read_manifest` / `verify_signed_plugin_source` / `verify_manifest` | filesystem/signature/trust validation。invalid schema/root は `ConfigurationError`、manifest/tree/signature は `PluginError`。 |
| `author_config` / `safe_app_settings` | author defaults / plugin-visible redacted application mapping。 |
| `write_catalog` / `trust_plugin` / `revoke_plugin` / `install_plugin` / `uninstall_plugin` | catalog/filesystem mutation。transaction and confirmation policy は [plugin package reference](plugin-package.md)。 |
| `PluginUninstallResult(id, removed_directory, removed_catalog_entry)` | `as_json()` は `plugin_id`、`removed_installation`、`removed_catalog` を返す。 |

`PluginConfigOverrides` は `Mapping[str, Mapping[str, Any]]`。outer key は plugin ID、inner value は mapping だけで scalar/list は不可。`PluginVerificationMode` は `strict|warn|off|bypass-all|bypass-catalog|bypass-signature`、persisted security value は最初の三つ、ephemeral override は三つの bypass 値だけを受ける。

<a id="api-errors"></a>

## Exceptions and caller handling

<!-- error-catalog:start -->
| exception | code | reason | public attributes |
| --- | --- | --- | --- |
| `ImageDownloaderError` | `image_downloader_error` | image downloader operation failed | — |
| `ConfigurationError` | `configuration_error` | configuration is invalid | — |
| `PluginError` | `plugin_error` | plugin operation failed | — |
| `UnsupportedSiteFeature` | `unsupported_site_feature` | site requires an unsupported feature | — |
| `AuthenticationError` | `authentication_error` | authentication failed | — |
| `SecretNotFound` | `secret_not_found` | required secret was not found | — |
| `RequestError` | `request_error` | HTTP request failed | — |
| `HttpTransportError` | `http_transport_error` | HTTP transport failed | — |
| `HttpStatusError` | `http_status_error` | HTTP server returned an error response | `status`, `response_url` |
| `RedirectPolicyError` | `redirect_policy_error` | HTTP redirect violates the configured policy | `request_url`, `redirect_url`, `response_url`, `http_status` |
| `ResponseSizeLimitError` | `response_size_limit_error` | HTTP response exceeds the configured byte limit | `response_url`, `http_status`, `limit_bytes` |
| `ImageProcessingError` | `image_processing_error` | image processing failed | — |
| `ImageDecodeError` | `image_decode_error` | image data cannot be decoded | — |
| `UnsupportedImageFormatError` | `unsupported_image_format` | image format is unsupported | `image_format` |
| `ImageContentTypeError` | `image_content_type_error` | image response has a non-image content type | — |
| `ImageMimeMismatchError` | `image_mime_mismatch` | declared image MIME does not match image data | — |
| `ImageDimensionLimitError` | `image_dimension_limit_error` | image dimensions exceed the configured pixel limit | — |
| `ImageWorkerError` | `image_worker_error` | image worker process failed | — |
| `ImageProcessorClosedError` | `image_processor_closed` | image processor is closed | — |
| `StorageError` | `storage_error` | storage operation failed | — |
| `OutputAllocationError` | `output_allocation_error` | could not allocate a unique output filename | — |
| `ExistingFileConflictError` | `existing_file_conflict` | output file already exists and existing-file=error prevents overwrite | `relative_path`, `policy` |
| `UpdateStateError` | `update_state_error` | update state is invalid or cannot be read | — |
| `StorageSafetyError` | `storage_safety_error` | storage operation would escape its trusted root | — |
| `InterProcessLockError` | `interprocess_lock_error` | inter-process lock operation failed | — |
<!-- error-catalog:end -->

### Constructors and diagnostic attributes

Most catalog exceptions use the inherited `Exception(message)` constructor and
the stable class `code`/`reason`. The exceptions below have additional public
constructor arguments or attributes. Attributes are diagnostic data, not a
promise that every value is non-null.

| exception | constructor | attributes |
| --- | --- | --- |
| `HttpStatusError` | `HttpStatusError(status: int, *, response_url: str | None = None)` | `status`, `response_url` |
| `RedirectPolicyError` | `RedirectPolicyError(message: str | None = None, *, request_url: str | None = None, redirect_url: str | None = None, http_status: int | None = None)` | `request_url`, `redirect_url`, `response_url` (the request URL), `http_status` |
| `ResponseSizeLimitError` | `ResponseSizeLimitError(message: str | None = None, *, response_url: str | None = None, http_status: int | None = None, limit_bytes: int | None = None)` | `response_url`, `http_status`, `limit_bytes` |
| `UnsupportedImageFormatError` | `UnsupportedImageFormatError(image_format: str)` | `image_format` |
| `ExistingFileConflictError` | `ExistingFileConflictError(relative_path: str | Path)` | `relative_path: Path`, `policy == "error"` |

`continue_on_image_error=true` の通常 fetch/process/save failure は `ImageOutcome.failure` に残る。authentication、configuration、plugin、storage safety、inter-process lock、fail-fast は result を返さず operation を中断する。`check_updates()` は complete `UpdateResult` または exception のいずれかで partial result を返さない。caller は具体例外を扱い、機械利用には stable `code`/`reason` を使い、raw exception message を契約として扱わない。

<a id="api-logging"></a>

logging contract は [logging reference](logging.md) が正本である。
