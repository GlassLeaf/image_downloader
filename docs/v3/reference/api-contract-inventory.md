<a id="api-v3-contract-inventory"></a>

# API v3の公開契約一覧

<a id="api-inventory"></a>

この一覧は、安定したファサードで公開する名前を網羅しています。個々の引数、戻り値、例外は[ライブラリAPIリファレンス](library-api.md)、[設定リファレンス](configuration.md)、[CLIリファレンス](cli.md)、[ログリファレンス](logging.md)で定義します。各ブロックが実装の<code>__all__</code>と一致することを、契約テストで確認します。

## image_downloader

<!-- api-inventory:start image_downloader -->
DownloadRequest
InspectRequest
UpdateCheckRequest
WorkflowPlanRequest
WorkflowRequest
WorkflowExecutionError
WorkflowPlanError
WorkflowCancelledError
WorkflowPlanCancelledError
AdditionalFileProvider
AdditionalFileReceiver
AdditionalFileSaveObserver
AdditionalFileHookContext
AdditionalFileHookPoint
AdditionalFileOutcome
AdditionalFileReceiveResult
AdditionalFileSaveResult
AdditionalFileSpec
WorkflowRetryTimeoutError
WorkflowImageResult
WorkflowAttemptResult
WorkflowRoundResult
WorkflowItemResult
WorkflowResult
WorkflowLogResult
WorkflowPlanLogError
WorkflowPlanItem
WorkflowPlanResult
WorkflowRunRecord
WorkflowStateItem
WorkflowStateView
WorkflowPruneResult
WorkflowStateService
AppConfig
apply_overrides
AuthFlow
AuthenticationError
Chapter
ChapterResult
ConfigurableSitePlugin
ConfigurationError
ArgumentError
DownloadManifest
DownloadResult
DownloadService
EffectiveRequestPreview
ExistingFileConflictError
FailureKind
HttpStatusError
HttpTransportError
ImageArtifact
ImageFetchRequest
ImageRequestResolution
ImageRequestResolutionFailure
ImageRequestResolutionStatus
ImageContentTypeError
ImageDecodeError
ImageDimensionLimitError
ImageDownloaderError
ImageFailure
ImageMimeMismatchError
ImageOutcome
ImageOutcomeKind
ImageProcessingError
ImageProcessorClosedError
ImageProcessor
ImageResource
ImageSaveOptions
ImageTransportMetadata
ImageWorkerError
InterProcessLockError
load_application_config
ManifestInspectionResult
OutputAllocationError
resolve_application_config
RedirectPolicyError
ResolvedApplicationConfig
OriginScopedAuthFlow
PluginError
PluginExecutionContext
PluginFormatContext
OutputFormatValueProvider
RequestPort
RequestError
RequestResponse
RequestSpec
ResponseSizeLimitError
RuntimeComposer
SecretNotFoundError
SecretProvider
SitePlugin
StorageError
StorageSafetyError
TransformContext
TransportCookie
TransportHeader
TransportRequestMetadata
UnsupportedImageFormatError
UnsupportedSiteFeatureError
UpdateCandidate
UpdateChange
UpdateChangeKind
UpdateProvider
UpdateResult
UpdateSnapshot
UpdateStateError
UpdateCheckUnsupportedError
<!-- api-inventory:end -->
## image_downloader.config

<!-- api-inventory:start image_downloader.config -->
AppConfig
DEFAULT_CONFIG
Profile
Output
ImageFormat
Media
ConsoleLogging
Download
Logging
Network
Email
Notification
NotificationMethod
NotificationCategory
PluginSettings
Security
Storage
Plugins
PluginDownloadPolicy
ImageProcessors
GenericHtmlFallback
Fallback
WorkflowHistory
WorkflowLogging
StrictModel
validate_config
deep_merge
ConfigurationLayer
ResolvedApplicationConfig
load_yaml
normalize_host
registrable_domain
site_file_name
load_application_config
resolve_application_config
apply_overrides
resolve_paths
plugin_root
default_user_config_path
default_data_root
default_plugin_root
<!-- api-inventory:end -->

## image_downloader.runtime

<!-- api-inventory:start image_downloader.runtime -->
DownloadRequest
InspectRequest
UpdateCheckRequest
WorkflowPlanRequest
WorkflowRequest
ArtifactPipeline
ChapterReporter
DownloadService
OutputAllocation
OutputAllocator
OutputFormatContext
RequestGateway
RuntimeComposer
RuntimeSecrets
UpdateState
_RuntimeDependencies
<!-- api-inventory:end -->

<code>_RuntimeDependencies</code>は先頭にアンダースコアがありますが、現在はファサードが明示的に再公開しているため、API v3の公開契約に含めます。将来の削除は別の互換性変更として扱います。

## image_downloader.security

<!-- api-inventory:start image_downloader.security -->
CatalogEntry
PluginCatalog
PluginConfigOverrides
PluginDownloadPolicyOverrides
PluginKind
PluginManifest
PluginVerificationMode
PluginVerificationOverride
PluginClassLoader
PluginDiagnostic
PluginDiscovery
PluginDiscoveryResult
PluginManifestVerifier
PluginRecord
PluginRegistry
PluginVerificationResult
PluginRuntime
PluginSelector
safe_app_settings
catalog_entry_for
install_plugin
revoke_plugin
trust_plugin
uninstall_plugin
PluginUninstallResult
author_config
canonical_jcs
catalog_path
effective_verification_mode
plugin_content_digest
read_manifest
verify_manifest
verify_signed_plugin_source
write_catalog
<!-- api-inventory:end -->

## image_downloader.cli

<!-- api-inventory:start image_downloader.cli -->
EXIT_SUCCESS
EXIT_FAILURE
EXIT_CONFIGURATION
EXIT_AUTHENTICATION
EXIT_PLUGIN
EXIT_PARTIAL
build_parser
run
doctor
config_command
plugin_command
main
<!-- api-inventory:end -->

## image_downloader.observability.logging

<!-- api-inventory:start image_downloader.observability.logging -->
LogRecord
ChapterFailureRecord
LogSink
ChapterFileSink
DebugFileSink
ConsoleSink
DownloadLogger
safe_log_text
mask_log_text
safe_exception_name
safe_locator
safe_relative_path
safe_url
<!-- api-inventory:end -->


## 追加したstorage操作

既存のファサードのexport集合は維持します。公開メンバーに`UpdateState.validate()`を追加しました。
storage層の操作境界には`FileSystem.shorten_component()`、`WorkflowState.read_feeds()`、不変の`WorkflowFeedSnapshot`、`source_key()`を設けています。
正確なシグネチャとcontext managerの扱いは[APIシグネチャ](api-signatures.md#storageの操作境界)を参照してください。
