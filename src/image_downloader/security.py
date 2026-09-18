"""Public compatibility imports for plugin security and runtime management."""

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
from .plugins.management import catalog_entry_for, install_plugin, revoke_plugin, trust_plugin
from .plugins.plugin_manifest import (
    CatalogEntry,
    PluginCatalog,
    PluginConfigOverrides,
    PluginKind,
    PluginManifest,
    author_config,
    canonical_jcs,
    catalog_path,
    read_manifest,
    verify_manifest,
    verify_signed_plugin_source,
    write_catalog,
)
from .plugins.runtime import PluginRuntime, PluginSelector, safe_app_settings

__all__ = [
    "CatalogEntry",
    "PluginCatalog",
    "PluginConfigOverrides",
    "PluginKind",
    "PluginManifest",
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
    "author_config",
    "canonical_jcs",
    "catalog_path",
    "read_manifest",
    "verify_manifest",
    "verify_signed_plugin_source",
    "write_catalog",
]
