# 完全 API 参照

この文書と [API contract inventory](api-contract-inventory.md) が library 利用者向けの API v3 参照仕様である。inventory にある facade export はすべて stable であり、ここにない internal module や private name は stable ではない。

<a id="api-common"></a>

## 共通規約

- <code>Path</code> は <code>pathlib.Path</code>、Mapping は読み取り専用として扱う。value object は frozen で、入力 mapping/sequence はコピーまたは freeze される。
- async method は await する。<code>asyncio.CancelledError</code> は通常の failure result に変換せず再送出する。
- custom exception は <code>ImageDownloaderError</code> の subclass で、<code>code</code> と <code>reason</code> を stable diagnostic field とする。raw exception message や URL を machine contract として解釈してはならない。
- filesystem を変更する API は明示する。その他の API は caller の config、plugin、network、credential provider による exception を送出し得る。

## root facade: types、protocol、結果

### Request と manifest value object

| type | constructor field と意味 |
| --- | --- |
| <code>RequestSpec</code> | <code>url: str</code>、<code>method="GET"</code>、headers/cookies/query/form mapping、<code>json=None</code>、<code>referer=None</code>、<code>auth_required=True</code>、<code>retry_non_idempotent=False</code>。form と json の同時指定は ValueError。 |
| <code>RequestResponse</code> | <code>url, status, headers, body</code>。gateway が返す immutable HTTP response。 |
| <code>ImageSaveOptions</code> | format、extension、quality、optimize、progressive、lossless、compress_level、exif。None は config/default に委ねる。 |
| <code>ImageResource</code> | <code>url</code> は absolute HTTP(S) URL、<code>index=1</code>、referer、headers、save_options、image_id。placeholder/None は不可。 |
| <code>Chapter</code> | number、title、subtitle、images、chapter_id。images は tuple 化される。 |
| <code>DownloadManifest</code> | title、chapters、content_id、author、access、revision、metadata。metadata は str から str の mapping だけを許可する。 |
| <code>ImageArtifact</code> | data bytes、content_type、source_url、image_id、extension、history。transform hook の入出力。 |

<a id="api-download-result"></a>

### download result

| type | field と利用規約 |
| --- | --- |
| <code>FailureKind</code> | FETCH、PROCESS、SAVE。 |
| <code>ImageFailure</code> | kind、exception_type、message、code、reason、response_url、http_status、output_path、transport。image/chapter/index を持たない。 |
| <code>ImageOutcomeKind</code> | SAVED、SKIPPED、FAILED。 |
| <code>ImageOutcome</code> | image、kind、path、failure。失敗と画像の対応はここで得る。 |
| <code>ChapterResult</code> | chapter と outcomes tuple。 |
| <code>DownloadResult</code> | source_url、manifest、chapters。saved_files/skipped_files は absolute path string の tuple、failures は ImageFailure の平坦化 tuple。 |

### update value object

<code>UpdateCandidate(url, content_id=None, revision=None)</code> は plugin が発見した対象、<code>UpdateSnapshot(source_url, candidates, checked_at)</code> は plugin の観測結果、<code>UpdateChange(kind, url, content_id=None, revision=None)</code> は state 比較結果、<code>UpdateResult(source_url, plugin_id, changes, checked_at)</code> は service の最終結果である。<code>UpdateChangeKind</code> は ADDED、CHANGED、REMOVED。

### protocol と context

| API | signature | 契約 |
| --- | --- | --- |
| <code>RequestPort.execute</code> | <code>async execute(spec: RequestSpec) -&gt; RequestResponse</code> | capability-reduced HTTP access。RequestError を送出し得る。 |
| <code>SecretProvider.get</code> | <code>get(name: str) -&gt; str</code> | 未設定・取得不能なら SecretNotFound。 |
| <code>PluginExecutionContext</code> | <code>(config, app_settings, manifest, catalog, secrets, requests)</code> | config、app_settings、manifest、catalog、secrets、requests property を提供する。request と secret を持つ hook 専用。 |
| <code>TransformContext</code> | <code>(image, config, app_settings, plugin_manifest, catalog, manifest, chapter, site_manifest=None, site_catalog=None)</code> | image_id、index、config、app_settings、plugin_manifest、catalog、site_manifest、site_catalog、manifest、chapter property。network/secret capability はない。 |
| <code>AuthFlow</code> | <code>is_auth_failure(request,response)-&gt;bool</code>、<code>async apply(request)-&gt;RequestSpec</code>、<code>async refresh(failed,response)-&gt;RequestSpec|None</code> | gateway が request に適用する。refresh None は回復不能。 |
| <code>OriginScopedAuthFlow</code> | AuthFlow + <code>allowed_origins</code> | 認証を追加 origin に送るための opt-in。 |
| <code>SitePlugin</code> | validate_config、matches、async inspect、async create_image_request、async recover_image_request、auth_flow、async transform_image | hook 回数と concurrency は [実行ライフサイクル](execution-lifecycle.md)。 |
| <code>ConfigurableSitePlugin</code> | <code>matches_with_config(url, config, app_settings)-&gt;bool</code> | selection 前、同期・副作用なし。 |
| <code>UpdateProvider</code> | <code>async check_updates(url, context)-&gt;UpdateSnapshot</code> | service update operation にだけ使う。 |
| <code>ImageProcessor</code> | <code>validate_config(config, app_settings)</code>、<code>async transform(artifact, context)-&gt;ImageArtifact</code> | processor chain の一要素。 |

<a id="api-configuration"></a>

## configuration facade

すべての config model は frozen Pydantic <code>StrictModel</code> であり、unknown field を拒否する。<code>model_validate</code>、<code>model_dump</code> などの Pydantic base API は Pydantic の契約に従う。

| model | stable field |
| --- | --- |
| <code>Profile</code> | default |
| <code>Storage</code> | data_root。null または absolute path。 |
| <code>Plugins</code> | root。null または absolute path。 |
| <code>Output</code> | directory_format、filename_format、existing_file、image_format、isolate_by_plugin、max_component_length、lock_timeout_seconds。 |
| <code>Media</code> | input_validation、content_type_mismatch、max_image_pixels。 |
| <code>ConsoleLogging</code> | enabled。 |
| <code>Logging</code> | console、safe_query_parameters、safe_fragment_parameters。sensitive な parameter 名は拒否。 |
| <code>Network</code> | request_concurrency、origin_request_concurrency、registrable_domain_request_concurrency、request/connect/read/write/pool timeout、max_attempts、retry_max_delay_seconds、auth_refresh_attempts、global_request_interval_seconds、pool_max_connections、pool_max_idle_connections、max_response_bytes、http2、follow_redirects、proxy、headers。 |
| <code>Email</code> | smtp_host、smtp_port、use_tls、from alias、to、username、credential_service。 |
| <code>Notification</code> | enabled、methods、notify_on、routes、desktop、email。NotificationMethod は desktop/email、NotificationCategory は event category literal。 |
| <code>Security</code> | plugin_verification: strict/warn/off。 |
| <code>Download</code> | chapter_concurrency、image_concurrency_per_chapter、continue_on_image_error、allow_empty_chapter_manifest。 |
| <code>ImageProcessors</code> | chain。重複しない reverse-DNS plugin id。 |
| <code>PluginSettings</code> | enabled、config、secrets。secret reference は upper-case environment reference。 |
| <code>GenericHtmlFallback</code> | enabled。 |
| <code>Fallback</code> | generic_html。 |
| <code>AppConfig</code> | profile、storage、plugins、output、media、logging、network、notification、security、download、image_processors、plugin_settings、fallback。 |

<code>DEFAULT_CONFIG: AppConfig</code> は schema default の immutable instance である。

| function/class | signature と結果 | exception・副作用 |
| --- | --- | --- |
| <code>validate_config</code> | <code>validate_config(value: Mapping|AppConfig) -&gt; AppConfig</code> | invalid schema は ConfigurationError。 |
| <code>deep_merge</code> | <code>deep_merge(base, override) -&gt; dict</code> | mapping を再帰 merge した新規 dict。入力を変更しない。 |
| <code>ConfigurationLayer</code> | <code>(role, path, status)</code> | resolver が検討した layer と状態。 |
| <code>ResolvedApplicationConfig</code> | <code>(config, main_config_path, config_root, source, selected_profile, layers, origins)</code>、<code>main_config_kind</code> property | 解決結果と説明 metadata。 |
| <code>load_yaml</code> | <code>load_yaml(path, required=False, config_root=None) -&gt; dict</code> | YAML を読む。ConfigurationError を送出し得る。 |
| <code>normalize_host</code> | <code>normalize_host(host) -&gt; tuple[str, bool]</code> | normalized host と URL 由来 flag。invalid host は ConfigurationError。 |
| <code>registrable_domain</code> | <code>registrable_domain(host) -&gt; str</code> | rate-limit grouping 用 domain。 |
| <code>site_file_name</code> | <code>site_file_name(host) -&gt; str</code> | site layer filename。 |
| <code>resolve_application_config</code> | <code>(path, profile=None, site=None, *, require_config=False, runtime_override=None, source=None, rewrite_user_layers=False) -&gt; ResolvedApplicationConfig</code> | layer を解決する。rewrite_user_layers true は user YAML を原子的に書換え得る。 |
| <code>load_application_config</code> | <code>(path, profile=None, site=None, *, require_config=False, runtime_override=None, rewrite_user_layers=False) -&gt; AppConfig</code> | 上の config のみを返す。 |
| <code>apply_overrides</code> | <code>(config, override) -&gt; AppConfig</code> | 新規 config を返す。profile override と invalid value は ConfigurationError。 |
| <code>resolve_paths</code> | <code>(config) -&gt; Mapping[str, Path]</code> | downloads/logs/state 等の absolute trusted roots。 |
| <code>plugin_root</code> | <code>(config) -&gt; Path</code> | effective plugin root。 |
| <code>default_user_config_path</code>、<code>default_data_root</code>、<code>default_plugin_root</code> | <code>() -&gt; Path</code> | platform default path。filesystem は変更しない。 |

## runtime facade

| API | signature と契約 |
| --- | --- |
| <code>RuntimeComposer</code> | <code>RuntimeComposer(config, *, config_root: Path, plugin_root: Path, plugin_verification_override=None)</code>。config_root は既存 directory 必須、plugin_root は存在しなくてもよい。<code>compose() -&gt; DownloadService</code> は所有 resource を構築し、<code>compose_registry() -&gt; PluginRuntime</code> は registry だけを構築する。 |
| <code>DownloadService</code> | <code>async run(url, *, plugin_overrides=None, fallback_override=None) -&gt; DownloadResult</code>、<code>async check_updates(...same options) -&gt; UpdateResult</code>、<code>async close() -&gt; None</code>、async context manager。operation は直列。close 後の run/update は RuntimeError。compose が通常の生成経路。 |
| <code>RequestGateway</code> | runtime HTTP gateway。<code>async execute(RequestSpec) -&gt; RequestResponse</code> と <code>async close()</code> を持つ。redirect、timeout、retry、auth、response size policy に従い RequestError subclass を送出する。 |
| <code>ArtifactPipeline</code> | selected site transform、validation、processor chain、save 用 runtime component。service が所有する。 |
| <code>ChapterReporter</code> | chapter log/report runtime component。service が所有する。 |
| <code>OutputAllocator</code> | <code>(filesystem, config)</code>、<code>chapter_directory(chapter)-&gt;Path</code>、<code>async allocate(chapter_directory,image,chapter,extension)-&gt;OutputAllocation</code>。collision policy に応じて ExistingFileConflictError または OutputAllocationError。 |
| <code>OutputAllocation</code> | relative_path、should_write property、<code>async commit()</code>、<code>async abort()</code>。reservation は一度だけ完了する。 |
| <code>RuntimeSecrets</code> | <code>(plugin_id, references)</code>、<code>get(name)-&gt;str</code>。environment/keyring を参照し、欠落時 SecretNotFound。 |
| <code>UpdateState</code> | <code>(filesystem, lock_timeout_seconds=30.0)</code>、records/save と <code>apply_snapshot</code> / <code>apply_snapshot_async</code>。state file を原子的に更新し、invalid state は UpdateStateError。 |
| <code>_RuntimeDependencies</code> | outputs、logs、state、events、logger、notifications、cookie_store、cookie_baseline、gateway、image_processor、output_locks を持つ composition DTO。advanced integration/test 用の stable export であり、field を省略して service を構築してはならない。 |

## security facade

| type | field、method、結果 |
| --- | --- |
| <code>PluginManifest</code> | value、signature、digest、directory、id property、kind property。read_manifest が生成する検証対象。 |
| <code>CatalogEntry</code> | id、kind、manifest_digest、selection_priority、revoked、publisher、version、public_key、key_id、file_tree_sha256、content_digest、content_pinned property、as_json()。 |
| <code>PluginCatalog</code> | <code>(entries)</code>、<code>load(path)</code>、find、replace、remove。entry ID の重複や schema は ConfigurationError。 |
| <code>PluginConfigOverrides</code> | operation に渡す plugin config override mapping。unknown/invalid override は ConfigurationError。 |
| <code>PluginKind</code> | site_plugin または image_processor_plugin literal。 |
| <code>PluginVerificationMode</code> | strict/warn/off と internal administrative bypass mode を表す literal。 |
| <code>PluginVerificationOverride</code> | runtime で verification policy を上書きする literal。 |
| <code>PluginDiagnostic</code> | name、source、loaded、detail、warning。 |
| <code>PluginRecord</code> | manifest、author_defaults、catalog、builtin、id/kind property。 |
| <code>PluginDiscoveryResult</code> | candidates、diagnostics。 |
| <code>PluginVerificationResult</code> | records、catalog、diagnostics。 |
| <code>PluginDiscovery</code> | <code>(plugin_root, mode="strict")</code>、<code>discover() -&gt; PluginDiscoveryResult</code>。directory structure を読む。 |
| <code>PluginManifestVerifier</code> | <code>(plugin_root, *, mode)</code>、<code>verify(discovery)-&gt;PluginVerificationResult</code>。catalog/manifest policy を適用。 |
| <code>PluginRegistry</code> | <code>(records=(), *, catalog=None)</code>、records property、<code>get(plugin_id)</code>。immutable record snapshot。 |
| <code>PluginClassLoader</code> | register_class、load_class、module_namespace、unload、close、construct、site_instance、processor_instance、typed_instance。imported plugin module lifecycle を所有し、PluginError を送出し得る。 |
| <code>PluginSelector</code> | <code>(loader)</code>、<code>select(records,url,config,overrides)</code>。matches/matches_with_config を評価し、競合や未選択を PluginError とする。 |
| <code>PluginRuntime</code> | <code>(config, plugin_root, *, mode)</code>、records/catalog property、register_builtin、validate_settings、enabled、effective_config、validate_operation_overrides、validate_candidate_overrides、site_instance、processor_instance、module_namespace、prepare、doctor_validate、validate_all、resolve、processor、close。verified plugin と instance lifecycle を所有する。 |
| <code>PluginUninstallResult</code> | plugin_id、removed_installation、removed_catalog、as_json()。 |

| function | signature、戻り値、副作用 |
| --- | --- |
| <code>canonical_jcs</code> | <code>(value) -&gt; bytes</code>。署名対象の canonical JSON。 |
| <code>catalog_path</code> | <code>(plugin_root) -&gt; Path</code>。catalog.json path。 |
| <code>effective_verification_mode</code> | <code>(configured, override) -&gt; PluginVerificationMode</code>。 |
| <code>read_manifest</code> | <code>(directory, *, mode="strict") -&gt; PluginManifest</code>。filesystem を読み、invalid input は PluginError/ConfigurationError。 |
| <code>plugin_content_digest</code> | <code>(directory) -&gt; str</code>。tree を読み digest を返す。 |
| <code>verify_signed_plugin_source</code> | <code>(manifest) -&gt; None</code>。署名/tree を検証し PluginError を送出。 |
| <code>verify_manifest</code> | <code>(manifest, catalog, *, mode) -&gt; CatalogEntry|None</code>。trust policy を検証し filesystem は変更しない。 |
| <code>author_config</code> | <code>(manifest) -&gt; Mapping</code>。manifest の author defaults。 |
| <code>safe_app_settings</code> | <code>(config) -&gt; Mapping</code>。plugin に渡せる redacted app setting。 |
| <code>catalog_entry_for</code> | <code>(manifest, *, selection_priority=0, revoked=False, content_pinned=False) -&gt; CatalogEntry</code>。 |
| <code>write_catalog</code> | <code>(plugin_root, catalog) -&gt; None</code>。catalog file を原子的に更新する。 |
| <code>trust_plugin</code> | <code>(plugin_root, directory, *, selection_priority=0, mode="strict") -&gt; CatalogEntry</code>。catalog を変更する。 |
| <code>revoke_plugin</code> | <code>(plugin_root, plugin_id) -&gt; CatalogEntry</code>。catalog entry を revoked に更新する。 |
| <code>install_plugin</code> | <code>(plugin_root, source, *, selection_priority=0, mode="strict") -&gt; CatalogEntry</code>。source を stage、検証、install、trust する filesystem mutation。 |
| <code>uninstall_plugin</code> | <code>(plugin_root, plugin_id) -&gt; PluginUninstallResult</code>。installation と catalog entry を transactional に削除する。 |

管理 API の invalid root/schema は ConfigurationError、signature/manifest/class contract の failure は PluginError、filesystem safety/lock failure は StorageError subclass として処理する。mutation 前後に caller が backup と audit を行う。

## CLI facade

| API | signature・結果 |
| --- | --- |
| EXIT_SUCCESS / EXIT_FAILURE / EXIT_CONFIGURATION / EXIT_AUTHENTICATION / EXIT_PLUGIN / EXIT_PARTIAL | CLI の stable integer exit status。意味は [CLI 参照](cli-reference.md)。 |
| <code>build_parser()</code> | <code>() -&gt; argparse.ArgumentParser</code>。v3 command parser。parse error は SystemExit。 |
| <code>async run(args)</code> | <code>(argparse.Namespace) -&gt; int</code>。parsed command を dispatch し exit status を返す。 |
| <code>async doctor(args, *, raise_errors=False)</code> | <code>-&gt; int</code>。raise_errors false では config/plugin error を render して exit status、true では再送出。 |
| <code>config_command(args)</code>、<code>plugin_command(args)</code> | <code>-&gt; int</code>。対応 command を処理し、invalid option は ValueError/ConfigurationError。 |
| <code>main(argv=None)</code> | <code>(Sequence[str]|None) -&gt; int</code>。parse、dispatch、error rendering を行う同期 CLI entry point。 |

<a id="api-logging"></a>

## observability.logging facade

| API | constructor / method と契約 |
| --- | --- |
| <code>LogRecord</code> | <code>(message, chapter_id=None, metadata=dict, trusted=False)</code>。<code>formatted()</code> は trusted でない message を mask する。 |
| <code>ChapterFailureRecord</code> | url、response_url、path、stage、image_index、http_status、code、reason、exception_type、message、transport を持つ immutable chapter-log input。 |
| <code>LogSink</code> | <code>async write(record)</code>、<code>async close()</code>。custom sink は並行 write を安全に扱う。 |
| <code>ChapterFileSink</code> | <code>(path=None, *, filesystem=None, relative_path=None)</code>。path または filesystem+relative_path の一方が必要、欠落時 ValueError。async write/close は file を直列化する。 |
| <code>DebugFileSink</code> | ChapterFileSink。write に UTC timestamp、chapter、module を加える。 |
| <code>ConsoleSink</code> | <code>(stream=None)</code>。async write が stream へ安全に出力する。 |
| <code>DownloadLogger</code> | <code>(sinks=None)</code>。configure_safety、safe_url、set_chapter_summary_console、begin/flush/end_python_log_capture、async core、register_chapter、async log、chapter_header/download/save/error_group/done/close_chapter、error_detail、debug、close を持つ。sink failure は diagnostic-only として抑制され得る。register_chapter の重複、capture の二重開始は RuntimeError。 |
| <code>safe_log_text</code> / <code>mask_log_text</code> | secret を redaction した text を返す。 |
| <code>safe_url</code> | URL の sensitive query/fragment を mask して返す。 |
| <code>safe_relative_path</code> | trusted root からの安全な表示 path を返す。 |
| <code>safe_exception_name</code> | allowlist にある exception class 名だけを返す。 |

<code>DownloadLogger.core</code> は event/module の allowlist に限定し、任意の raw message を受け取らない。<code>close()</code> は capture を終え、sink を閉じる。logger API を直接使う caller は、close と同時に write しないこと、記録内容を result/error source of truth にしないことを守る。

## Public method reference

<a id="api-public-methods"></a>

この節は facade export の constructor と public method/property の詳細参照である。Pydantic 基底の `model_validate`、`model_dump` 等の継承 API と underscore private member はここでは独自契約にしない。async と明記した method は await が必要である。特記のない method は caller の input/config/plugin/filesystem error を対応する `ImageDownloaderError` subclass として送出し得る。

### Runtime components

| class | constructor / member | return, side effect, failure |
| --- | --- | --- |
| `RuntimeComposer` | `(config, *, config_root, plugin_root, plugin_verification_override=None)`; `compose()`; `compose_registry()` | config/root を検証し所有 resource を構築。各々 `DownloadService` / `PluginRuntime`。composer は compose 後の service lifetime を管理しない。 |
| `DownloadService` | `(config, registry, dependencies)`; async `run(url, *, plugin_overrides=None, fallback_override=None)`; async `check_updates` (same args); async `close()`; async context manager | run is `DownloadResult`, update is `UpdateResult`; one instance serializes operations. close releases owned resources, is idempotent; run/update after close raise `RuntimeError`. Cancellation propagates. |
| `RequestGateway` | `(config, cookie_jar=None, *, logger=None)`; `operation(*, plugin_id, operation_url, auth_flow_factory=None, invoker=None)`; async `execute(spec)`; async `close()` | operation returns scoped `OperationRequestGateway`; execute returns `RequestResponse` after redirect/retry/auth/size policy. HTTP failures are `RequestError` family. close invalidates network use. |
| `ArtifactPipeline` | `(config, registry, site_record, overrides, image_processor, logger, invoker, processors)`; async `process(plugin, artifact, image, manifest, chapter)` | validates input/output, site transform then configured processors; returns `ImageArtifact`; processor/media errors are `ImageProcessingError` family. |
| `ChapterReporter` | `(filesystem, chapter_directory, manifest, chapter, logger, reporter_id)`; `start()`; `record(position, outcome)`; `finish(outcomes=None)` | records deterministic chapter reporting. `finish` accepts precomputed outcomes or collected positions; writes report/log effects. |
| `OutputAllocator` | `(filesystem, config)`; `refresh_directory(directory)`; `chapter_directory(chapter)`; async `allocate(chapter_directory, image, chapter, extension)` | directory/path allocation and locks. `allocate` returns reservation `OutputAllocation`; collision policy can raise `ExistingFileConflictError` or `OutputAllocationError`. |
| `OutputAllocation` | `(relative_path, _allocator=None, _key=None, _completion=None, _finished=False)`; property `should_write`; async `commit()`; async `abort()` | a reservation; `should_write` signals skip vs write. Exactly one terminal action is valid; double completion raises `RuntimeError`. |
| `RuntimeSecrets` | `(plugin_id, references)`; `get(name)` | resolves one logical name from environment/keyring; returns string or raises `SecretNotFound`. |
| `UpdateState` | `(filesystem, *, lock_timeout_seconds=30.0)`; `records()`; `save(records)`; `apply_snapshot(plugin_id, source_url, snapshot)`; async `apply_snapshot_async(...)` | reads/writes update-state atomically; apply returns `tuple[UpdateChange, ...]`. Corrupt state/lock/storage errors use `UpdateStateError`/storage errors. |
| `_RuntimeDependencies` | `(outputs, logs, state, events, logger, notifications, cookie_store, cookie_baseline, gateway, image_processor, output_locks)` | composition DTO. All fields are required and are internally coordinated resources; advanced integrations must supply compatible, still-open instances. |

### Security and plugin runtime

| class | constructor / member | return, side effect, failure |
| --- | --- | --- |
| `CatalogEntry` | fields in [catalog](#security-facade); property `content_pinned`; `as_json()` | returns normal or content-pin JSON shape; no mutation. |
| `PluginCatalog` | `(entries)`; classmethod `load(path)`; `find(plugin_id)`; `replace(entry)`; `remove(plugin_id)` | immutable catalog replacement operations; returns entry/`None` or new catalog. Invalid/duplicate schema is `ConfigurationError`. |
| `PluginDiscovery` | `(plugin_root, *, mode="strict")`; `discover()` | reads source layout and returns `PluginDiscoveryResult`, including diagnostics. |
| `PluginManifestVerifier` | `(plugin_root, *, mode)`; `verify(discovery)` | applies catalog/mode and returns records/catalog/diagnostics. Strict trust failure is reported/filtered according to discovery policy. |
| `PluginRegistry` | `(records=(), *, catalog=None)`; property `records`; `get(plugin_id)` | immutable record snapshot and optional record lookup. |
| `PluginClassLoader` | `()`; `register_class(record, plugin_class)`; `load_class(record)`; `module_namespace(record)`; `unload(record)`; `close()`; `construct(record)`; `site_instance(record)`; `processor_instance(record)`; `typed_instance(record)` | imports isolated source modules and creates/caches typed instances. `close` unloads modules. Invalid entry/class/protocol is `PluginError`; do not call instances after loader close. |
| `PluginSelector` | `(loader)`; `select(records, url, *, fallback_enabled, overrides, enabled, effective_config, app_settings)` | returns `(record, site_plugin, selection_diagnostics)` under documented priorities; conflict/no candidate is `PluginError`. |
| `PluginRuntime` | `(config, plugin_root, *, mode)`; properties `records`, `catalog`; `register_builtin`; `validate_settings`; `enabled`; `effective_config`; `validate_operation_overrides`; `validate_candidate_overrides`; `site_instance`; `processor_instance`; `module_namespace`; `prepare`; `doctor_validate`; `validate_all`; `resolve`; `processor`; `close` | owns verified registry/loader. Validation methods return `None`; effective config is immutable mapping; resolve returns selected record/site; processor returns tuple or `None` when disabled. Invalid configuration is `ConfigurationError`, source/type failure is `PluginError`. `close` unloads modules. |
| `PluginUninstallResult` | `(id, removed_directory, removed_catalog_entry)`; `as_json()` | result of destructive uninstall; JSON keys are `plugin_id`, `removed_installation`, `removed_catalog`. |

`PluginConfigOverrides` is `Mapping[str, Mapping[str, Any]]`: outer keys are plugin IDs and each value is a JSON/YAML-object-like config mapping, never a scalar/list. `PluginVerificationMode` is the literal union `strict|warn|off|bypass-all|bypass-catalog|bypass-signature`; persisted `Security.plugin_verification` accepts only the first three. `PluginVerificationOverride` accepts only the three bypass values and is ephemeral.

### Logging methods

| API | signature / behavior |
| --- | --- |
| `LogRecord` | `(message, chapter_id=None, metadata={}, trusted=False)`; `formatted()` returns redacted display text. |
| `ChapterFailureRecord` | `(url, response_url, path, stage, image_index, http_status, code, reason, exception_type, message, transport=None)` immutable safe detail input. |
| `LogSink` / file/console sinks | async `write(record)` and async `close()`. `ChapterFileSink(path=None, *, filesystem=None, relative_path=None)` requires exactly usable path source; `DebugFileSink` adds timestamp/chapter/module; `ConsoleSink(stream=None)` writes safe formatted output. |
| `DownloadLogger` lifecycle | `(sinks=None)`; `configure_safety(logging, output_root=None)`, `safe_url`, `set_chapter_summary_console`, `begin_python_log_capture(namespaces)`, `flush_python_log_capture`, `end_python_log_capture`, and `close` configure/flush/release sinks. Duplicate capture start and duplicate chapter registration raise `RuntimeError`. |
| `DownloadLogger` records | `core(event, *, module, chapter_id=None, url=None, path=None, method=None, status=None, bytes_count=None, count=None, plugin_id=None, attempt=None, action=None, error=None, debug=False, include_chapter=False)`, `register_chapter`, `log`, `chapter_header`, `chapter_download`, `chapter_save`, `chapter_error_group`, `chapter_done`, `close_chapter`, `error_detail`, `debug` | synchronous safe logging façade. `core` uses event/module allowlists; sink failure is diagnostic-only where possible. Caller must not race `close` with writes. |

`safe_log_text`/`mask_log_text` redact arbitrary text; `safe_url` masks sensitive URL parts; `safe_relative_path` represents a path under a trusted root; `safe_exception_name` allowlists a class name. These helpers are pure formatting boundaries and never make a raw secret safe for storage.

<a id="api-errors"></a>

## 例外 catalog と処理

| exception | code | reason | public attribute |
| --- | --- | --- | --- |
| ImageDownloaderError | image_downloader_error | image downloader operation failed | — |
| ConfigurationError | configuration_error | configuration is invalid | — |
| PluginError | plugin_error | plugin operation failed | — |
| UnsupportedSiteFeature | unsupported_site_feature | site requires an unsupported feature | — |
| AuthenticationError | authentication_error | authentication failed | — |
| SecretNotFound | secret_not_found | required secret was not found | — |
| RequestError | request_error | HTTP request failed | — |
| HttpTransportError | http_transport_error | HTTP transport failed | — |
| HttpStatusError | http_status_error | HTTP server returned an error response | status, response_url |
| RedirectPolicyError | redirect_policy_error | HTTP redirect violates the configured policy | request_url, redirect_url, response_url, http_status |
| ResponseSizeLimitError | response_size_limit_error | HTTP response exceeds the configured byte limit | response_url, http_status, limit_bytes |
| ImageProcessingError | image_processing_error | image processing failed | — |
| ImageDecodeError | image_decode_error | image data cannot be decoded | — |
| UnsupportedImageFormatError | unsupported_image_format | image format is unsupported | image_format |
| ImageContentTypeError | image_content_type_error | image response has a non-image content type | — |
| ImageMimeMismatchError | image_mime_mismatch | declared image MIME does not match image data | — |
| ImageDimensionLimitError | image_dimension_limit_error | image dimensions exceed the configured pixel limit | — |
| ImageWorkerError | image_worker_error | image worker process failed | — |
| ImageProcessorClosedError | image_processor_closed | image processor is closed | — |
| StorageError | storage_error | storage operation failed | — |
| OutputAllocationError | output_allocation_error | could not allocate a unique output filename | — |
| ExistingFileConflictError | existing_file_conflict | output file already exists and existing-file=error prevents overwrite | relative_path, policy |
| UpdateStateError | update_state_error | update state is invalid or cannot be read | — |
| StorageSafetyError | storage_safety_error | storage operation would escape its trusted root | — |
| InterProcessLockError | interprocess_lock_error | inter-process lock operation failed | — |

| 状況 | 呼出側の扱い |
| --- | --- |
| continue_on_image_error が有効な通常の画像 fetch/process/save failure | ImageOutcome.failure を検査し、必要なら outcome.image と対応付けて report する。 |
| AuthenticationError、ConfigurationError、PluginError、StorageSafetyError、InterProcessLockError、fail-fast | result は返らない。例外を呼出境界で処理し、service を close する。 |
| check_updates failure | partial UpdateResult は返らない。例外を処理する。 |
| cancellation | CancelledError を再送出し、service close 時の永続化完了を await する。 |
| close 後の run/check_updates、二重 allocation/capture | RuntimeError は caller misuse。new service/logger/allocation lifecycle を作る。 |
