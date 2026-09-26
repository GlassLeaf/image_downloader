# Public signature index

This is the signature-level reference for the stable facades in the [API inventory](api-contract-inventory.md). Each ordinary callable has one visible signature and one `api-contract` marker. `async` methods must be awaited. For meaning, side effects, and handling, use [library API](library-api.md).

## Contract categories

| public kind | complete contract source |
| --- | --- |
| ordinary function/concrete class | the individually marked constructor, own public method, and own property on this page |
| frozen dataclass DTO | constructor and fields in the DTO tables below |
| Pydantic model | declared fields/defaults/Literal constraints in [configuration](configuration.md#config-schema); inherited Pydantic methods are excluded |
| Protocol | exact hook signature in [plugin hooks](plugin-hooks.md) |
| enum/type alias | member `.value` / Literal alternatives in [library API](library-api.md#api-download-result) and [plugin package](plugin-package.md#plugin-catalog) |
| exception | [error catalog](library-api.md#api-errors), including its own constructor and attributes; inherited `Exception` members are excluded |

## Configuration and CLI functions

| callable | visible signature |
| --- | --- |
| `validate_config` <!-- api-contract: image_downloader.config.validate_config --> | `validate_config(value: Mapping[str, Any] | AppConfig) -> AppConfig` |
| `deep_merge` <!-- api-contract: image_downloader.config.deep_merge --> | `deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]` |
| `load_yaml` <!-- api-contract: image_downloader.config.load_yaml --> | `load_yaml(path: Path, *, required: bool = False, config_root: Path | None = None) -> dict[str, Any]` |
| `normalize_host` <!-- api-contract: image_downloader.config.normalize_host --> | `normalize_host(host: str) -> tuple[str, bool]` |
| `registrable_domain` <!-- api-contract: image_downloader.config.registrable_domain --> | `registrable_domain(host: str) -> str` |
| `site_file_name` <!-- api-contract: image_downloader.config.site_file_name --> | `site_file_name(host: str) -> str` |
| `load_application_config` <!-- api-contract: image_downloader.config.load_application_config --> | `load_application_config(path: Path, profile: str | None = None, site: str | None = None, *, require_config: bool = False, runtime_override: Mapping[str, Any] | None = None, rewrite_user_layers: bool = False) -> AppConfig` |
| `resolve_application_config` <!-- api-contract: image_downloader.config.resolve_application_config --> | `resolve_application_config(path: Path | None, profile: str | None = None, site: str | None = None, *, require_config: bool = False, runtime_override: Mapping[str, Any] | None = None, source: Literal["defaults", "user", "explicit"] | None = None, rewrite_user_layers: bool = False) -> ResolvedApplicationConfig` |
| `apply_overrides` <!-- api-contract: image_downloader.config.apply_overrides --> | `apply_overrides(config: AppConfig, override: Mapping[str, Any]) -> AppConfig` |
| `resolve_paths` <!-- api-contract: image_downloader.config.resolve_paths --> | `resolve_paths(config: AppConfig) -> Mapping[str, Path]` |
| `plugin_root` <!-- api-contract: image_downloader.config.plugin_root --> | `plugin_root(config: AppConfig) -> Path` |
| `default_user_config_path` <!-- api-contract: image_downloader.config.default_user_config_path --> | `default_user_config_path() -> Path` |
| `default_data_root` <!-- api-contract: image_downloader.config.default_data_root --> | `default_data_root() -> Path` |
| `default_plugin_root` <!-- api-contract: image_downloader.config.default_plugin_root --> | `default_plugin_root() -> Path` |
| `build_parser` <!-- api-contract: image_downloader.cli.build_parser --> | `build_parser() -> argparse.ArgumentParser` |
| `run` <!-- api-contract: image_downloader.cli.run --> | `async run(args: argparse.Namespace) -> int` |
| `doctor` <!-- api-contract: image_downloader.cli.doctor --> | `async doctor(args: argparse.Namespace, *, raise_errors: bool = False) -> int` |
| `config_command` <!-- api-contract: image_downloader.cli.config_command --> | `config_command(args: argparse.Namespace) -> int` |
| `plugin_command` <!-- api-contract: image_downloader.cli.plugin_command --> | `plugin_command(args: argparse.Namespace) -> int` |
| `main` <!-- api-contract: image_downloader.cli.main --> | `main(argv: Sequence[str] | None = None) -> int` |

`AppConfig`, `Profile`, `Storage`, `Plugins`, `Output`, `Media`, `ConsoleLogging`, `Download`, `Logging`, `Network`, `Email`, `Notification`, `PluginSettings`, `Security`, `ImageProcessors`, `GenericHtmlFallback`, and `Fallback` are keyword-only Pydantic models. Their complete declared field/default/Literal contracts are the [configuration schema](configuration.md#config-schema); Pydantic-inherited methods are intentionally outside the stable API contract.

| configuration DTO | declared constructor / fields |
| --- | --- |
| `ConfigurationLayer` | `ConfigurationLayer(role: str, path: Path | None, status: LayerStatus)` |
| `ResolvedApplicationConfig` | `ResolvedApplicationConfig(config: AppConfig, main_config_path: Path | None, config_root: Path, source: str, selected_profile: str, layers: tuple[ConfigurationLayer, ...], origins: Mapping[str, str])` |

## Frozen root DTOs and protocols

| DTO | declared constructor / fields |
| --- | --- |
| `Chapter` | `Chapter(number: int, title: str, subtitle: str = "", images: tuple[ImageResource, ...] = (), chapter_id: str | None = None)` |
| `ChapterResult` | `ChapterResult(chapter: Chapter, outcomes: tuple[ImageOutcome, ...])` |
| `DownloadManifest` | `DownloadManifest(title: str, chapters: tuple[Chapter, ...], content_id: str | None = None, author: str | None = None, access: str | None = None, revision: str | None = None, metadata: Mapping[str, str] = <factory: freeze_mapping>)` |
| `DownloadResult` | `DownloadResult(source_url: str, manifest: DownloadManifest, chapters: tuple[ChapterResult, ...])` |
| `ImageArtifact` | `ImageArtifact(data: bytes, content_type: str, source_url: str, image_id: str | None = None, extension: str | None = None, history: tuple[str, ...] = ())` |
| `ImageFailure` | `ImageFailure(kind: FailureKind, exception_type: str, message: str, code: str = "unexpected_image_failure", reason: str = "unexpected image failure", response_url: str | None = None, http_status: int | None = None, output_path: str | None = None, transport: str | None = None)` |
| `ImageOutcome` | `ImageOutcome(image: ImageResource, kind: ImageOutcomeKind, path: str | None = None, failure: ImageFailure | None = None)` |
| `ImageResource` | `ImageResource(url: str, index: int = 1, referer: str | None = None, headers: Mapping[str, str] = <factory: freeze_mapping>, save_options: ImageSaveOptions = <factory: ImageSaveOptions>, image_id: str | None = None)` |
| `ImageSaveOptions` | `ImageSaveOptions(format: str | None = None, extension: str | None = None, quality: int | None = None, optimize: bool | None = None, progressive: bool | None = None, lossless: bool | None = None, compress_level: int | None = None, exif: bool = False)` |
| `RequestSpec` | `RequestSpec(url: str, method: str = "GET", headers: Mapping[str, str] = <factory: freeze_mapping>, cookies: Mapping[str, str] = <factory: freeze_mapping>, referer: str | None = None, query: Mapping[str, str] = <factory: freeze_mapping>, form: Mapping[str, str] = <factory: freeze_mapping>, json: object | None = None, auth_required: bool = True, retry_non_idempotent: bool = False)` |
| `RequestResponse` | `RequestResponse(url: str, status: int, headers: Mapping[str, str], body: bytes)` |
| `UpdateCandidate` | `UpdateCandidate(url: str, content_id: str | None = None, revision: str | None = None)` |
| `UpdateSnapshot` | `UpdateSnapshot(source_url: str, candidates: tuple[UpdateCandidate, ...], checked_at: datetime)` |
| `UpdateChange` | `UpdateChange(kind: UpdateChangeKind, url: str, content_id: str | None = None, revision: str | None = None)` |
| `UpdateResult` | `UpdateResult(source_url: str, plugin_id: str, changes: tuple[UpdateChange, ...], checked_at: datetime)` |

`<factory: name>` is the dataclass default-factory spelling: a new value is built for every construction, not a shared mutable default. `RequestPort`, `SecretProvider`, `AuthFlow`, `OriginScopedAuthFlow`, `SitePlugin`, `ConfigurableSitePlugin`, `ImageProcessor`, and `UpdateProvider` are Protocols; their exact signatures and capability boundaries are in [plugin hooks](plugin-hooks.md). `FailureKind`, `ImageOutcomeKind`, and `UpdateChangeKind` are enum contracts in [library API](library-api.md#api-download-result).

## Runtime concrete classes

| constructor, method, or property | visible signature |
| --- | --- |
| `ArtifactPipeline` <!-- api-contract: image_downloader.runtime.ArtifactPipeline --> | `ArtifactPipeline(config: AppConfig, registry: PluginRuntime, site_record: PluginRecord, overrides: PluginConfigOverrides | None, image_processor: ImageProcessor, logger: DownloadLogger, invoker: PluginInvoker, processors: tuple[PreparedProcessor | str, ...])` |
| `ArtifactPipeline.process` <!-- api-contract: image_downloader.runtime.ArtifactPipeline.process --> | `async process(self, plugin: SitePlugin, artifact: ImageArtifact, image: ImageResource, manifest: DownloadManifest, chapter: Chapter) -> ImageArtifact` |
| `ChapterReporter` <!-- api-contract: image_downloader.runtime.ChapterReporter --> | `ChapterReporter(filesystem: FileSystem, chapter_directory: Path, manifest: DownloadManifest, chapter: Chapter, logger: DownloadLogger, reporter_id: str)` |
| `ChapterReporter.start` <!-- api-contract: image_downloader.runtime.ChapterReporter.start --> | `async start(self) -> None` |
| `ChapterReporter.record` <!-- api-contract: image_downloader.runtime.ChapterReporter.record --> | `async record(self, position: int, outcome: ImageOutcome) -> None` |
| `ChapterReporter.finish` <!-- api-contract: image_downloader.runtime.ChapterReporter.finish --> | `async finish(self, outcomes: tuple[ImageOutcome, ...] | None = None) -> None` |
| `DownloadService` <!-- api-contract: image_downloader.runtime.DownloadService --> | `DownloadService(config: AppConfig, registry: PluginRuntime, dependencies: _RuntimeDependencies)` |
| `DownloadService.run` <!-- api-contract: image_downloader.runtime.DownloadService.run --> | `async run(self, url: str, *, plugin_overrides: PluginConfigOverrides | None = None, fallback_override: bool | None = None) -> DownloadResult` |
| `DownloadService.check_updates` <!-- api-contract: image_downloader.runtime.DownloadService.check_updates --> | `async check_updates(self, url: str, *, plugin_overrides: PluginConfigOverrides | None = None, fallback_override: bool | None = None) -> UpdateResult` |
| `DownloadService.close` <!-- api-contract: image_downloader.runtime.DownloadService.close --> | `async close(self) -> None` |
| `OutputAllocation` <!-- api-contract: image_downloader.runtime.OutputAllocation --> | `OutputAllocation(relative_path: Path, _allocator: OutputAllocator | None = None, _key: str | None = None, _completion: asyncio.Future[bool] | None = None, _finished: bool = False)` |
| `OutputAllocation.should_write` <!-- api-contract: image_downloader.runtime.OutputAllocation.should_write --> | `should_write(self) -> bool` property |
| `OutputAllocation.commit` <!-- api-contract: image_downloader.runtime.OutputAllocation.commit --> | `async commit(self) -> None` |
| `OutputAllocation.abort` <!-- api-contract: image_downloader.runtime.OutputAllocation.abort --> | `async abort(self) -> None` |
| `OutputAllocator` <!-- api-contract: image_downloader.runtime.OutputAllocator --> | `OutputAllocator(filesystem: FileSystem, config: AppConfig)` |
| `OutputAllocator.refresh_directory` <!-- api-contract: image_downloader.runtime.OutputAllocator.refresh_directory --> | `async refresh_directory(self, directory: Path) -> None` |
| `OutputAllocator.chapter_directory` <!-- api-contract: image_downloader.runtime.OutputAllocator.chapter_directory --> | `chapter_directory(self, chapter: Chapter) -> Path` |
| `OutputAllocator.allocate` <!-- api-contract: image_downloader.runtime.OutputAllocator.allocate --> | `async allocate(self, chapter_directory: Path, image: ImageResource, chapter: Chapter, extension: str) -> OutputAllocation` |
| `RequestGateway` <!-- api-contract: image_downloader.runtime.RequestGateway --> | `RequestGateway(config: AppConfig, cookie_jar: CookieJar | None = None, *, logger: DownloadLogger | None = None)` |
| `RequestGateway.operation` <!-- api-contract: image_downloader.runtime.RequestGateway.operation --> | `operation(self, *, plugin_id: str | None, operation_url: str, auth_flow_factory: Callable[[OperationRequestGateway], object | None] | None = None, invoker: PluginInvoker | None = None) -> OperationRequestGateway` |
| `RequestGateway.execute` <!-- api-contract: image_downloader.runtime.RequestGateway.execute --> | `async execute(self, spec: RequestSpec) -> RequestResponse` |
| `RequestGateway.close` <!-- api-contract: image_downloader.runtime.RequestGateway.close --> | `async close(self) -> None` |
| `RuntimeComposer` <!-- api-contract: image_downloader.runtime.RuntimeComposer --> | `RuntimeComposer(config: AppConfig, *, config_root: Path, plugin_root: Path, plugin_verification_override: PluginVerificationOverride | None = None)` |
| `RuntimeComposer.compose` <!-- api-contract: image_downloader.runtime.RuntimeComposer.compose --> | `compose(self) -> DownloadService` |
| `RuntimeComposer.compose_registry` <!-- api-contract: image_downloader.runtime.RuntimeComposer.compose_registry --> | `compose_registry(self) -> PluginRuntime` |
| `RuntimeSecrets` <!-- api-contract: image_downloader.runtime.RuntimeSecrets --> | `RuntimeSecrets(plugin_id: str, references: Mapping[str, str])` |
| `RuntimeSecrets.get` <!-- api-contract: image_downloader.runtime.RuntimeSecrets.get --> | `get(self, name: str) -> str` |
| `UpdateState` <!-- api-contract: image_downloader.runtime.UpdateState --> | `UpdateState(filesystem: FileSystem, *, lock_timeout_seconds: float = 30.0)` |
| `UpdateState.records` <!-- api-contract: image_downloader.runtime.UpdateState.records --> | `records(self) -> dict[str, _Record]` |
| `UpdateState.save` <!-- api-contract: image_downloader.runtime.UpdateState.save --> | `save(self, records: Mapping[str, Mapping[str, str | None]]) -> None` |
| `UpdateState.apply_snapshot` <!-- api-contract: image_downloader.runtime.UpdateState.apply_snapshot --> | `apply_snapshot(self, plugin_id: str, source_url: str, snapshot: UpdateSnapshot) -> tuple[UpdateChange, ...]` |
| `UpdateState.apply_snapshot_async` <!-- api-contract: image_downloader.runtime.UpdateState.apply_snapshot_async --> | `async apply_snapshot_async(self, plugin_id: str, source_url: str, snapshot: UpdateSnapshot) -> tuple[UpdateChange, ...]` |

`_RuntimeDependencies` is a classified dataclass DTO with no own public methods/properties: `_RuntimeDependencies(outputs, logs, state, events, logger, notifications, cookie_store, cookie_baseline, gateway, image_processor, output_locks)`.

## Plugin security API

`PluginConfigOverrides` is `Mapping[str, Mapping[str, Any]]`; `PluginKind`, `PluginVerificationMode`, and `PluginVerificationOverride` have the exact Literal alternatives defined in [plugin package](plugin-package.md#plugin-catalog).

| DTO | declared constructor / fields |
| --- | --- |
| `CatalogEntry` | `CatalogEntry(id: str, kind: PluginKind, manifest_digest: str, selection_priority: int, revoked: bool, publisher: str | None = None, version: str | None = None, public_key: str | None = None, key_id: str | None = None, file_tree_sha256: str | None = None, content_digest: str | None = None)` |
| `PluginManifest` | `PluginManifest(value: Mapping[str, Any], signature: str | None, digest: str, directory: Path)` |
| `PluginDiagnostic` | `PluginDiagnostic(name: str, source: str, loaded: bool, detail: str = "", warning: bool = False)` |
| `PluginDiscoveryResult` | `PluginDiscoveryResult(candidates: tuple[DiscoveredPlugin, ...], diagnostics: tuple[PluginDiagnostic, ...])` |
| `PluginRecord` | `PluginRecord(manifest: PluginManifest, author_defaults: Mapping[str, Any], catalog: CatalogEntry | None, builtin: bool = False)` |
| `PluginVerificationResult` | `PluginVerificationResult(records: tuple[PluginRecord, ...], catalog: PluginCatalog | None, diagnostics: tuple[PluginDiagnostic, ...])` |
| `PluginUninstallResult` | `PluginUninstallResult(id: str, removed_directory: bool, removed_catalog_entry: bool)` |

| constructor, method, or property | visible signature |
| --- | --- |
| `CatalogEntry.content_pinned` <!-- api-contract: image_downloader.security.CatalogEntry.content_pinned --> | `content_pinned(self) -> bool` property |
| `CatalogEntry.as_json` <!-- api-contract: image_downloader.security.CatalogEntry.as_json --> | `as_json(self) -> dict[str, object]` |
| `PluginCatalog` <!-- api-contract: image_downloader.security.PluginCatalog --> | `PluginCatalog(entries: tuple[CatalogEntry, ...])` |
| `PluginCatalog.load` <!-- api-contract: image_downloader.security.PluginCatalog.load --> | `load(path: Path) -> PluginCatalog` class method |
| `PluginCatalog.find` <!-- api-contract: image_downloader.security.PluginCatalog.find --> | `find(self, plugin_id: str) -> CatalogEntry | None` |
| `PluginCatalog.replace` <!-- api-contract: image_downloader.security.PluginCatalog.replace --> | `replace(self, entry: CatalogEntry) -> PluginCatalog` |
| `PluginCatalog.remove` <!-- api-contract: image_downloader.security.PluginCatalog.remove --> | `remove(self, plugin_id: str) -> PluginCatalog` |
| `PluginManifest.id` <!-- api-contract: image_downloader.security.PluginManifest.id --> | `id(self) -> str` property |
| `PluginManifest.kind` <!-- api-contract: image_downloader.security.PluginManifest.kind --> | `kind(self) -> PluginKind` property |
| `PluginClassLoader` <!-- api-contract: image_downloader.security.PluginClassLoader --> | `PluginClassLoader()` |
| `PluginClassLoader.register_class` <!-- api-contract: image_downloader.security.PluginClassLoader.register_class --> | `register_class(self, record: PluginRecord, plugin_class: type[object]) -> None` |
| `PluginClassLoader.load_class` <!-- api-contract: image_downloader.security.PluginClassLoader.load_class --> | `load_class(self, record: PluginRecord) -> type[object]` |
| `PluginClassLoader.module_namespace` <!-- api-contract: image_downloader.security.PluginClassLoader.module_namespace --> | `module_namespace(self, record: PluginRecord) -> str | None` |
| `PluginClassLoader.unload` <!-- api-contract: image_downloader.security.PluginClassLoader.unload --> | `unload(self, record: PluginRecord) -> None` |
| `PluginClassLoader.close` <!-- api-contract: image_downloader.security.PluginClassLoader.close --> | `close(self) -> None` |
| `PluginClassLoader.construct` <!-- api-contract: image_downloader.security.PluginClassLoader.construct --> | `construct(self, record: PluginRecord) -> object` |
| `PluginClassLoader.site_instance` <!-- api-contract: image_downloader.security.PluginClassLoader.site_instance --> | `site_instance(self, record: PluginRecord) -> SitePlugin` |
| `PluginClassLoader.processor_instance` <!-- api-contract: image_downloader.security.PluginClassLoader.processor_instance --> | `processor_instance(self, record: PluginRecord) -> ImageProcessor` |
| `PluginClassLoader.typed_instance` <!-- api-contract: image_downloader.security.PluginClassLoader.typed_instance --> | `typed_instance(self, record: PluginRecord) -> SitePlugin | ImageProcessor` |
| `PluginDiscovery` <!-- api-contract: image_downloader.security.PluginDiscovery --> | `PluginDiscovery(plugin_root: Path, *, mode: PluginVerificationMode = "strict")` |
| `PluginDiscovery.discover` <!-- api-contract: image_downloader.security.PluginDiscovery.discover --> | `discover(self) -> PluginDiscoveryResult` |
| `PluginManifestVerifier` <!-- api-contract: image_downloader.security.PluginManifestVerifier --> | `PluginManifestVerifier(plugin_root: Path, *, mode: PluginVerificationMode)` |
| `PluginManifestVerifier.verify` <!-- api-contract: image_downloader.security.PluginManifestVerifier.verify --> | `verify(self, discovery: PluginDiscoveryResult) -> PluginVerificationResult` |
| `PluginRecord.id` <!-- api-contract: image_downloader.security.PluginRecord.id --> | `id(self) -> str` property |
| `PluginRecord.kind` <!-- api-contract: image_downloader.security.PluginRecord.kind --> | `kind(self) -> PluginKind` property |
| `PluginRegistry` <!-- api-contract: image_downloader.security.PluginRegistry --> | `PluginRegistry(records: Mapping[str, PluginRecord] | tuple[PluginRecord, ...] = (), *, catalog: PluginCatalog | None = None)` |
| `PluginRegistry.records` <!-- api-contract: image_downloader.security.PluginRegistry.records --> | `records(self) -> Mapping[str, PluginRecord]` property |
| `PluginRegistry.get` <!-- api-contract: image_downloader.security.PluginRegistry.get --> | `get(self, plugin_id: str) -> PluginRecord | None` |
| `PluginRuntime` <!-- api-contract: image_downloader.security.PluginRuntime --> | `PluginRuntime(config: AppConfig, plugin_root: Path, *, mode: PluginVerificationMode)` |
| `PluginRuntime.records` <!-- api-contract: image_downloader.security.PluginRuntime.records --> | `records(self) -> Mapping[str, PluginRecord]` property |
| `PluginRuntime.catalog` <!-- api-contract: image_downloader.security.PluginRuntime.catalog --> | `catalog(self) -> PluginCatalog | None` property |
| `PluginRuntime.register_builtin` <!-- api-contract: image_downloader.security.PluginRuntime.register_builtin --> | `register_builtin(self, plugin_class: type[object]) -> None` |
| `PluginRuntime.validate_settings` <!-- api-contract: image_downloader.security.PluginRuntime.validate_settings --> | `validate_settings(self) -> None` |
| `PluginRuntime.enabled` <!-- api-contract: image_downloader.security.PluginRuntime.enabled --> | `enabled(self, record: PluginRecord) -> bool` |
| `PluginRuntime.effective_config` <!-- api-contract: image_downloader.security.PluginRuntime.effective_config --> | `effective_config(self, record: PluginRecord, overrides: PluginConfigOverrides | None = None) -> Mapping[str, Any]` |
| `PluginRuntime.validate_operation_overrides` <!-- api-contract: image_downloader.security.PluginRuntime.validate_operation_overrides --> | `validate_operation_overrides(self, site_record: PluginRecord, overrides: PluginConfigOverrides | None) -> None` |
| `PluginRuntime.validate_candidate_overrides` <!-- api-contract: image_downloader.security.PluginRuntime.validate_candidate_overrides --> | `validate_candidate_overrides(self, overrides: PluginConfigOverrides | None) -> None` |
| `PluginRuntime.site_instance` <!-- api-contract: image_downloader.security.PluginRuntime.site_instance --> | `site_instance(self, record: PluginRecord) -> SitePlugin` |
| `PluginRuntime.processor_instance` <!-- api-contract: image_downloader.security.PluginRuntime.processor_instance --> | `processor_instance(self, record: PluginRecord) -> ImageProcessor` |
| `PluginRuntime.module_namespace` <!-- api-contract: image_downloader.security.PluginRuntime.module_namespace --> | `module_namespace(self, record: PluginRecord) -> str | None` |
| `PluginRuntime.close` <!-- api-contract: image_downloader.security.PluginRuntime.close --> | `close(self) -> None` |
| `PluginRuntime.prepare` <!-- api-contract: image_downloader.security.PluginRuntime.prepare --> | `prepare(self) -> None` |
| `PluginRuntime.doctor_validate` <!-- api-contract: image_downloader.security.PluginRuntime.doctor_validate --> | `doctor_validate(self, overrides: PluginConfigOverrides | None = None) -> None` |
| `PluginRuntime.validate_all` <!-- api-contract: image_downloader.security.PluginRuntime.validate_all --> | `validate_all(self, overrides: PluginConfigOverrides | None = None) -> None` |
| `PluginRuntime.resolve` <!-- api-contract: image_downloader.security.PluginRuntime.resolve --> | `resolve(self, url: str, *, fallback_enabled: bool, overrides: PluginConfigOverrides | None = None) -> tuple[PluginRecord, SitePlugin]` |
| `PluginRuntime.processor` <!-- api-contract: image_downloader.security.PluginRuntime.processor --> | `processor(self, plugin_id: str, overrides: PluginConfigOverrides | None = None) -> tuple[PluginRecord, ImageProcessor] | None` |
| `PluginSelector` <!-- api-contract: image_downloader.security.PluginSelector --> | `PluginSelector(loader: PluginClassLoader)` |
| `PluginSelector.select` <!-- api-contract: image_downloader.security.PluginSelector.select --> | `select(self, records: Mapping[str, PluginRecord], url: str, *, fallback_enabled: bool, overrides: PluginConfigOverrides | None, enabled: Callable[[PluginRecord], bool], effective_config: Callable[[PluginRecord, PluginConfigOverrides | None], Mapping[str, Any]], app_settings: Mapping[str, Any]) -> tuple[PluginRecord, SitePlugin, tuple[Mapping[str, Any], ...]]` |
| `catalog_entry_for` <!-- api-contract: image_downloader.security.catalog_entry_for --> | `catalog_entry_for(manifest: PluginManifest, *, selection_priority: int = 0, revoked: bool = False, content_pinned: bool = False) -> CatalogEntry` |
| `install_plugin` <!-- api-contract: image_downloader.security.install_plugin --> | `install_plugin(plugin_root: Path, source: Path, *, selection_priority: int = 0, mode: PluginVerificationMode = "strict") -> CatalogEntry` |
| `revoke_plugin` <!-- api-contract: image_downloader.security.revoke_plugin --> | `revoke_plugin(plugin_root: Path, plugin_id: str) -> CatalogEntry` |
| `trust_plugin` <!-- api-contract: image_downloader.security.trust_plugin --> | `trust_plugin(plugin_root: Path, directory: Path, *, selection_priority: int = 0, mode: PluginVerificationMode = "strict") -> CatalogEntry` |
| `uninstall_plugin` <!-- api-contract: image_downloader.security.uninstall_plugin --> | `uninstall_plugin(plugin_root: Path, plugin_id: str) -> PluginUninstallResult` |
| `PluginUninstallResult.as_json` <!-- api-contract: image_downloader.security.PluginUninstallResult.as_json --> | `as_json(self) -> dict[str, object]` |
| `safe_app_settings` <!-- api-contract: image_downloader.security.safe_app_settings --> | `safe_app_settings(config: AppConfig) -> Mapping[str, Any]` |
| `author_config` <!-- api-contract: image_downloader.security.author_config --> | `author_config(manifest: PluginManifest) -> Mapping[str, Any]` |
| `canonical_jcs` <!-- api-contract: image_downloader.security.canonical_jcs --> | `canonical_jcs(value: object) -> bytes` |
| `catalog_path` <!-- api-contract: image_downloader.security.catalog_path --> | `catalog_path(plugin_root: Path) -> Path` |
| `effective_verification_mode` <!-- api-contract: image_downloader.security.effective_verification_mode --> | `effective_verification_mode(configured: Literal["strict", "warn", "off"], override: PluginVerificationOverride | None = None) -> PluginVerificationMode` |
| `plugin_content_digest` <!-- api-contract: image_downloader.security.plugin_content_digest --> | `plugin_content_digest(directory: Path) -> str` |
| `read_manifest` <!-- api-contract: image_downloader.security.read_manifest --> | `read_manifest(directory: Path, *, mode: PluginVerificationMode = "strict") -> PluginManifest` |
| `verify_manifest` <!-- api-contract: image_downloader.security.verify_manifest --> | `verify_manifest(manifest: PluginManifest, catalog: PluginCatalog | None, *, mode: PluginVerificationMode) -> CatalogEntry | None` |
| `verify_signed_plugin_source` <!-- api-contract: image_downloader.security.verify_signed_plugin_source --> | `verify_signed_plugin_source(manifest: PluginManifest) -> None` |
| `write_catalog` <!-- api-contract: image_downloader.security.write_catalog --> | `write_catalog(plugin_root: Path, catalog: PluginCatalog) -> None` |

## Logging API

`LogRecord` and `ChapterFailureRecord` are DTOs. `LogSink` is the async sink contract; concrete sinks own their output destination.

| constructor, method, or property | visible signature |
| --- | --- |
| `LogRecord` | `LogRecord(message: str, chapter_id: str | None = None, metadata: dict[str, str] = <factory: dict>, trusted: bool = False)` |
| `LogRecord.formatted` <!-- api-contract: image_downloader.observability.logging.LogRecord.formatted --> | `formatted(self) -> str` |
| `ChapterFailureRecord` | `ChapterFailureRecord(url: str, response_url: str | None, path: str | Path | None, stage: str, image_index: int, http_status: int | None, code: str, reason: str, exception_type: str, message: str, transport: str | None = None)` |
| `LogSink` <!-- api-contract: image_downloader.observability.logging.LogSink --> | `LogSink()` |
| `LogSink.write` <!-- api-contract: image_downloader.observability.logging.LogSink.write --> | `async write(self, record: LogRecord) -> None` |
| `LogSink.close` <!-- api-contract: image_downloader.observability.logging.LogSink.close --> | `async close(self) -> None` |
| `ChapterFileSink` <!-- api-contract: image_downloader.observability.logging.ChapterFileSink --> | `ChapterFileSink(path: Path | None = None, *, filesystem: FileSystem | None = None, relative_path: Path | None = None)` |
| `ChapterFileSink.write` <!-- api-contract: image_downloader.observability.logging.ChapterFileSink.write --> | `async write(self, record: LogRecord) -> None` |
| `ChapterFileSink.close` <!-- api-contract: image_downloader.observability.logging.ChapterFileSink.close --> | `async close(self) -> None` |
| `DebugFileSink` <!-- api-contract: image_downloader.observability.logging.DebugFileSink --> | `DebugFileSink(path: Path | None = None, *, filesystem: FileSystem | None = None, relative_path: Path | None = None)` |
| `DebugFileSink.write` <!-- api-contract: image_downloader.observability.logging.DebugFileSink.write --> | `async write(self, record: LogRecord) -> None` |
| `DebugFileSink.close` <!-- api-contract: image_downloader.observability.logging.DebugFileSink.close --> | `async close(self) -> None` |
| `ConsoleSink` <!-- api-contract: image_downloader.observability.logging.ConsoleSink --> | `ConsoleSink(stream: TextIO | None = None)` |
| `ConsoleSink.write` <!-- api-contract: image_downloader.observability.logging.ConsoleSink.write --> | `async write(self, record: LogRecord) -> None` |
| `DownloadLogger` <!-- api-contract: image_downloader.observability.logging.DownloadLogger --> | `DownloadLogger(sinks: list[LogSink] | None = None)` |
| `DownloadLogger.configure_safety` <!-- api-contract: image_downloader.observability.logging.DownloadLogger.configure_safety --> | `configure_safety(self, logging: dict[str, object], output_root: str | Path | None = None) -> None` |
| `DownloadLogger.safe_url` <!-- api-contract: image_downloader.observability.logging.DownloadLogger.safe_url --> | `safe_url(self, value: str) -> str` |
| `DownloadLogger.set_chapter_summary_console` <!-- api-contract: image_downloader.observability.logging.DownloadLogger.set_chapter_summary_console --> | `set_chapter_summary_console(self, enabled: bool) -> None` |
| `DownloadLogger.begin_python_log_capture` <!-- api-contract: image_downloader.observability.logging.DownloadLogger.begin_python_log_capture --> | `begin_python_log_capture(self, namespaces: Iterable[str]) -> None` |
| `DownloadLogger.flush_python_log_capture` <!-- api-contract: image_downloader.observability.logging.DownloadLogger.flush_python_log_capture --> | `async flush_python_log_capture(self) -> None` |
| `DownloadLogger.end_python_log_capture` <!-- api-contract: image_downloader.observability.logging.DownloadLogger.end_python_log_capture --> | `end_python_log_capture(self) -> None` |
| `DownloadLogger.register_chapter` <!-- api-contract: image_downloader.observability.logging.DownloadLogger.register_chapter --> | `register_chapter(self, chapter_id: str, path: Path | None = None, *, filesystem: FileSystem | None = None, relative_path: Path | None = None) -> None` |
| `DownloadLogger.core` <!-- api-contract: image_downloader.observability.logging.DownloadLogger.core --> | `async core(self, event: str, *, module: str, chapter_id: str | None = None, url: str | None = None, path: str | Path | None = None, method: str | None = None, status: int | None = None, bytes_count: int | None = None, count: int | None = None, plugin_id: str | None = None, attempt: int | None = None, action: str | None = None, error: Exception | None = None, debug: bool = False, include_chapter: bool = False) -> None` |
| `DownloadLogger.log` <!-- api-contract: image_downloader.observability.logging.DownloadLogger.log --> | `async log(self, message: str, *, chapter_id: str | None = None, module: str = "library") -> None` |
| `DownloadLogger.chapter_header` <!-- api-contract: image_downloader.observability.logging.DownloadLogger.chapter_header --> | `async chapter_header(self, chapter_id: str, *, url: str, title: str, subtitle: str | None) -> None` |
| `DownloadLogger.chapter_download` <!-- api-contract: image_downloader.observability.logging.DownloadLogger.chapter_download --> | `async chapter_download(self, chapter_id: str, url: str) -> None` |
| `DownloadLogger.chapter_save` <!-- api-contract: image_downloader.observability.logging.DownloadLogger.chapter_save --> | `async chapter_save(self, chapter_id: str, path: str | Path) -> None` |
| `DownloadLogger.chapter_error_group` <!-- api-contract: image_downloader.observability.logging.DownloadLogger.chapter_error_group --> | `async chapter_error_group(self, chapter_id: str, category: str, records: list[ChapterFailureRecord]) -> None` |
| `DownloadLogger.chapter_done` <!-- api-contract: image_downloader.observability.logging.DownloadLogger.chapter_done --> | `async chapter_done(self, chapter_id: str) -> None` |
| `DownloadLogger.close_chapter` <!-- api-contract: image_downloader.observability.logging.DownloadLogger.close_chapter --> | `async close_chapter(self, chapter_id: str) -> None` |
| `DownloadLogger.error_detail` <!-- api-contract: image_downloader.observability.logging.DownloadLogger.error_detail --> | `async error_detail(self, error: Exception, *, chapter_id: str | None = None, url: str | None = None, path: str | Path | None = None, module: str = "download") -> None` |
| `DownloadLogger.debug` <!-- api-contract: image_downloader.observability.logging.DownloadLogger.debug --> | `async debug(self, message: str, *, chapter_id: str | None = None, module: str = "library", **metadata: str) -> None` |
| `DownloadLogger.close` <!-- api-contract: image_downloader.observability.logging.DownloadLogger.close --> | `async close(self) -> None` |
| `safe_log_text` <!-- api-contract: image_downloader.observability.logging.safe_log_text --> | `safe_log_text(value: str, *, maximum: int = 4096) -> str` |
| `mask_log_text` <!-- api-contract: image_downloader.observability.logging.mask_log_text --> | `mask_log_text(value: str, *, safe_query_parameters: set[str] | None = None, safe_fragment_parameters: set[str] | None = None) -> str` |
| `safe_exception_name` <!-- api-contract: image_downloader.observability.logging.safe_exception_name --> | `safe_exception_name(value: object) -> str` |
| `safe_relative_path` <!-- api-contract: image_downloader.observability.logging.safe_relative_path --> | `safe_relative_path(value: str | Path, output_root: Path | None) -> str` |
| `safe_url` <!-- api-contract: image_downloader.observability.logging.safe_url --> | `safe_url(value: str, *, safe_query_parameters: set[str] | None = None, safe_fragment_parameters: set[str] | None = None) -> str` |
