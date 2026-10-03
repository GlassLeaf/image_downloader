# API v3 contract inventory

<a id="api-inventory"></a>

この inventory は stable facade の完全な名前一覧である。個々の引数、戻り値、例外は [library API reference](library-api.md)、[configuration reference](configuration.md)、[CLI reference](cli.md)、[logging reference](logging.md) に定義する。各 block は contract test が実装の <code>__all__</code> と一致することを確認する。

## image_downloader

<!-- api-inventory:start image_downloader -->
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
SecretNotFound
SecretProvider
SitePlugin
StorageError
StorageSafetyError
TransformContext
TransportCookie
TransportHeader
TransportRequestMetadata
UnsupportedImageFormatError
UnsupportedSiteFeature
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

<code>_RuntimeDependencies</code> は先頭 underscore を持つが、現在は facade が明示的に re-export しているため API v3 contract に含める。将来の除去は別の互換性変更として扱う。

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
