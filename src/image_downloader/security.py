"""Public compatibility imports for plugin security and runtime management."""

from image_downloader.plugins.plugin_catalog import PluginCatalog, catalog_path, write_catalog
from image_downloader.plugins.plugin_files import canonical_jcs
from image_downloader.plugins.plugin_manifest import (
    CatalogEntry,
    PluginConfigOverrides,
    PluginDownloadPolicyOverrides,
    PluginKind,
    PluginManifest,
    PluginVerificationMode,
    PluginVerificationOverride,
    author_config,
    effective_verification_mode,
    read_manifest,
)
from image_downloader.plugins.plugin_verification import (
    plugin_content_digest,
    verify_manifest,
    verify_signed_plugin_source,
)

from .plugins.lifecycle import (
    PluginClassLoader,
    PluginDiagnostic,
    PluginDiscovery,
    PluginDiscoveryResult,
    PluginManifestVerifier,
    PluginRecord,
    PluginRegistry,
    PluginVerificationResult,
)
from .plugins.management import (
    PluginUninstallResult,
    catalog_entry_for,
    install_plugin,
    revoke_plugin,
    trust_plugin,
    uninstall_plugin,
)
from .plugins.runtime import PluginRuntime, PluginSelector, safe_app_settings

__all__ = [
    "CatalogEntry",
    "PluginCatalog",
    "PluginConfigOverrides",
    "PluginDownloadPolicyOverrides",
    "PluginKind",
    "PluginManifest",
    "PluginVerificationMode",
    "PluginVerificationOverride",
    "PluginClassLoader",
    "PluginDiagnostic",
    "PluginDiscovery",
    "PluginDiscoveryResult",
    "PluginManifestVerifier",
    "PluginRecord",
    "PluginRegistry",
    "PluginVerificationResult",
    "PluginRuntime",
    "PluginSelector",
    "safe_app_settings",
    "catalog_entry_for",
    "install_plugin",
    "revoke_plugin",
    "trust_plugin",
    "uninstall_plugin",
    "PluginUninstallResult",
    "author_config",
    "canonical_jcs",
    "catalog_path",
    "effective_verification_mode",
    "plugin_content_digest",
    "read_manifest",
    "verify_manifest",
    "verify_signed_plugin_source",
    "write_catalog",
]
