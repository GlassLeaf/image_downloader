from __future__ import annotations

import importlib
import re
from dataclasses import fields
from inspect import signature
from pathlib import Path

from image_downloader import (
    AppConfig,
    ExistingFileConflictError,
    ImageFailure,
    apply_overrides,
    load_application_config,
    resolve_application_config,
)
from image_downloader.exceptions import ERROR_CATALOG
from image_downloader.runtime import DownloadService, RuntimeComposer
from image_downloader.storage import FileSystem
from image_downloader.storage.cookies import CookieStore

LINK = re.compile(r"\[[^\]]*\]\((?P<target>[^)#]+)(?:#[^)]+)?\)")
INVENTORY = re.compile(
    r"<!-- api-inventory:start (?P<module>[a-z_.]+) -->\s*(?P<names>.*?)<!-- api-inventory:end -->",
    re.DOTALL,
)

EXPECTED_EXPORTS = {
    "image_downloader": tuple(
        """
        AppConfig apply_overrides AuthFlow AuthenticationError Chapter ChapterResult ConfigurableSitePlugin
        ConfigurationError DownloadManifest DownloadResult DownloadService ExistingFileConflictError FailureKind
        HttpStatusError HttpTransportError ImageArtifact ImageContentTypeError ImageDecodeError
        ImageDimensionLimitError ImageDownloaderError ImageFailure ImageMimeMismatchError ImageOutcome
        ImageOutcomeKind ImageProcessingError ImageProcessorClosedError ImageProcessor ImageResource
        ImageSaveOptions ImageWorkerError InterProcessLockError load_application_config OutputAllocationError
        resolve_application_config RedirectPolicyError ResolvedApplicationConfig OriginScopedAuthFlow PluginError
        PluginExecutionContext RequestPort RequestError RequestResponse RequestSpec ResponseSizeLimitError
        RuntimeComposer SecretNotFound SecretProvider SitePlugin StorageError StorageSafetyError TransformContext
        UnsupportedImageFormatError UnsupportedSiteFeature UpdateCandidate UpdateChange UpdateChangeKind
        UpdateProvider UpdateResult UpdateSnapshot UpdateStateError
        """.split()
    ),
    "image_downloader.config": tuple(
        """
        AppConfig DEFAULT_CONFIG Profile Output Media ConsoleLogging Download Logging Network Email Notification
        NotificationMethod NotificationCategory PluginSettings Security Storage Plugins ImageProcessors
        GenericHtmlFallback Fallback StrictModel validate_config deep_merge ConfigurationLayer
        ResolvedApplicationConfig load_yaml normalize_host registrable_domain site_file_name
        load_application_config resolve_application_config apply_overrides resolve_paths plugin_root
        default_user_config_path default_data_root default_plugin_root
        """.split()
    ),
    "image_downloader.runtime": tuple(
        """
        ArtifactPipeline ChapterReporter DownloadService OutputAllocation OutputAllocator RequestGateway
        RuntimeComposer RuntimeSecrets UpdateState _RuntimeDependencies
        """.split()
    ),
    "image_downloader.security": tuple(
        """
        CatalogEntry PluginCatalog PluginConfigOverrides PluginKind PluginManifest PluginVerificationMode
        PluginVerificationOverride PluginClassLoader PluginDiagnostic PluginDiscovery PluginDiscoveryResult
        PluginManifestVerifier PluginRecord PluginRegistry PluginVerificationResult PluginRuntime PluginSelector
        safe_app_settings catalog_entry_for install_plugin revoke_plugin trust_plugin uninstall_plugin
        PluginUninstallResult author_config canonical_jcs catalog_path effective_verification_mode
        plugin_content_digest read_manifest verify_manifest verify_signed_plugin_source write_catalog
        """.split()
    ),
    "image_downloader.cli": tuple(
        """
        EXIT_SUCCESS EXIT_FAILURE EXIT_CONFIGURATION EXIT_AUTHENTICATION EXIT_PLUGIN EXIT_PARTIAL build_parser
        run doctor config_command plugin_command main
        """.split()
    ),
    "image_downloader.observability.logging": tuple(
        """
        LogRecord ChapterFailureRecord LogSink ChapterFileSink DebugFileSink ConsoleSink DownloadLogger
        safe_log_text mask_log_text safe_exception_name safe_relative_path safe_url
        """.split()
    ),
}


def _documented_inventory(repository_root: Path) -> dict[str, tuple[str, ...]]:
    reference = (repository_root / "docs" / "v3" / "api-contract-inventory.md").read_text(encoding="utf-8")
    return {
        match.group("module"): tuple(
            line.strip() for line in match.group("names").splitlines() if line.strip()
        )
        for match in INVENTORY.finditer(reference)
    }


def test_public_configuration_helpers_are_available_from_package_root(tmp_path: Path) -> None:
    config_path = tmp_path / "app.yaml"
    config_path.write_text("storage: {data_root: null}\nplugins: {root: null}\n", encoding="utf-8")

    loaded = load_application_config(config_path, require_config=True)
    resolved = resolve_application_config(config_path, require_config=True)
    overridden = apply_overrides(AppConfig(), {"output": {"image_format": "PNG"}})

    assert loaded.storage.data_root is not None
    assert resolved.config == loaded
    assert overridden.output.image_format == "PNG"


def test_existing_file_conflict_is_a_public_storage_error() -> None:
    error = ExistingFileConflictError("chapter/0001.jpeg")

    assert error.code == "existing_file_conflict"
    assert error.relative_path == Path("chapter/0001.jpeg")
    assert error.policy == "error"


def test_cookie_store_requires_the_v3_filesystem_boundary(tmp_path: Path) -> None:
    store = CookieStore(FileSystem((tmp_path / "cookie").resolve()))

    assert store.path.name == "cookies.enc"
    assert list(store.load()) == []


def test_stable_facades_match_the_handwritten_contract_and_inventory(repository_root: Path) -> None:
    documented = _documented_inventory(repository_root)

    assert set(documented) == set(EXPECTED_EXPORTS)
    for module_name, expected in EXPECTED_EXPORTS.items():
        module = importlib.import_module(module_name)
        assert tuple(module.__all__) == expected
        assert documented[module_name] == expected


def test_representative_signatures_and_result_dtos_are_stable() -> None:
    assert tuple(signature(load_application_config).parameters) == (
        "path",
        "profile",
        "site",
        "require_config",
        "runtime_override",
        "rewrite_user_layers",
    )
    assert tuple(signature(resolve_application_config).parameters) == (
        "path",
        "profile",
        "site",
        "require_config",
        "runtime_override",
        "source",
        "rewrite_user_layers",
    )
    assert tuple(signature(RuntimeComposer).parameters) == (
        "config",
        "config_root",
        "plugin_root",
        "plugin_verification_override",
    )
    assert tuple(signature(DownloadService.run).parameters) == (
        "self",
        "url",
        "plugin_overrides",
        "fallback_override",
    )
    assert tuple(signature(DownloadService.check_updates).parameters) == (
        "self",
        "url",
        "plugin_overrides",
        "fallback_override",
    )
    assert tuple(signature(DownloadService.close).parameters) == ("self",)
    assert tuple(field.name for field in fields(ImageFailure)) == (
        "kind",
        "exception_type",
        "message",
        "code",
        "reason",
        "response_url",
        "http_status",
        "output_path",
        "transport",
    )
    assert tuple(AppConfig.model_fields) == (
        "profile",
        "storage",
        "plugins",
        "output",
        "media",
        "logging",
        "network",
        "notification",
        "security",
        "download",
        "image_processors",
        "plugin_settings",
        "fallback",
    )


def test_current_documentation_links_and_contracts_are_valid(repository_root: Path) -> None:
    documents = (
        repository_root / "README.md",
        repository_root / "docs" / "README.md",
        *(repository_root / "docs" / "v3").glob("*.md"),
        repository_root / "plugin-sources" / "README.md",
        repository_root / "examples" / "plugin-v3-template" / "README.md",
        repository_root / "examples" / "legacy" / "v2" / "README.md",
    )
    assert not (repository_root / "docs" / "legacy").exists()
    assert (repository_root / "archive" / "docs" / "legacy" / "v1" / "README.md").is_file()
    assert (repository_root / "archive" / "docs" / "legacy" / "v2" / "README.md").is_file()
    assert (repository_root / "archive" / "docs" / "legacy" / "v3-pre-reorg" / "README.md").is_file()
    for document in documents:
        for match in LINK.finditer(document.read_text(encoding="utf-8")):
            target = match.group("target")
            if target.startswith(("http:", "https:", "mailto:")):
                continue
            assert (document.parent / target).exists(), f"broken link in {document}: {target}"

    library_api = (repository_root / "docs" / "v3" / "library-api.md").read_text(encoding="utf-8")
    assert "from image_downloader import RuntimeComposer, load_application_config" in library_api
    assert "from image_downloader.config import" not in library_api
    lifecycle = (repository_root / "docs" / "v3" / "execution-lifecycle.md").read_text(encoding="utf-8")
    for required_topic in (
        "create_image_request",
        "recover_image_request",
        "ImageResource.url",
        "network.auth_refresh_attempts",
        "UnsupportedSiteFeature",
    ):
        assert required_topic in lifecycle

    reference = (repository_root / "docs" / "v3" / "api-reference.md").read_text(encoding="utf-8")
    for entry in ERROR_CATALOG:
        assert f"| {entry.exception_name} | {entry.code} | {entry.reason} |" in reference
