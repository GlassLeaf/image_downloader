from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path

import image_downloader
import image_downloader.cli as cli
import image_downloader.config as config
import image_downloader.output.output_allocator as output_allocator
import image_downloader.plugins.plugin_manifest as plugin_manifest
import image_downloader.runtime as runtime
import image_downloader.security as security
from image_downloader import AppConfig, DownloadService, RuntimeComposer
from image_downloader.application import composer, service
from image_downloader.commands import dispatch, doctor
from image_downloader.commands import parser as cli_parser
from image_downloader.configuration import layers, models
from image_downloader.observability import logging
from image_downloader.plugins import lifecycle as plugin_lifecycle
from image_downloader.plugins import management as plugin_management
from image_downloader.plugins import runtime as plugin_runtime
from image_downloader.privacy import log_safety
from image_downloader.transport import gateway


def test_all_package_modules_import_without_cycles() -> None:
    modules = pkgutil.walk_packages(image_downloader.__path__, prefix="image_downloader.")
    for module in modules:
        if module.name != "image_downloader.__main__":
            importlib.import_module(module.name)


def _relative_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.level > 0}


def test_facades_preserve_moved_public_imports() -> None:
    assert AppConfig is config.AppConfig is models.AppConfig
    assert config.load_application_config is layers.load_application_config
    assert DownloadService is runtime.DownloadService is service.DownloadService
    assert RuntimeComposer is runtime.RuntimeComposer is composer.RuntimeComposer
    assert cli.main is dispatch.main
    assert cli.doctor is doctor.doctor
    assert cli.build_parser is cli_parser.build_parser
    assert runtime.RequestGateway is gateway.RequestGateway
    assert runtime.OutputAllocator is output_allocator.OutputAllocator
    assert runtime.OutputAllocation is output_allocator.OutputAllocation
    assert security.PluginManifest is plugin_manifest.PluginManifest
    assert security.PluginCatalog is plugin_manifest.PluginCatalog
    assert security.read_manifest is plugin_manifest.read_manifest
    assert security.PluginRecord is plugin_lifecycle.PluginRecord
    assert security.PluginClassLoader is plugin_lifecycle.PluginClassLoader
    assert security.PluginDiscovery is plugin_lifecycle.PluginDiscovery
    assert security.PluginRegistry is plugin_lifecycle.PluginRegistry
    assert security.PluginRuntime is plugin_runtime.PluginRuntime
    assert security.install_plugin is plugin_management.install_plugin
    assert logging.mask_log_text is log_safety.mask_log_text
    assert logging.safe_url is log_safety.safe_url


def test_leaf_modules_do_not_import_their_compatibility_facades(repository_root: Path) -> None:
    source_root = repository_root / "src" / "image_downloader"
    boundaries = {
        "commands/parser.py": "cli",
        "commands/setup.py": "cli",
        "commands/download.py": "cli",
        "commands/cookie.py": "cli",
        "commands/doctor.py": "cli",
        "commands/config.py": "cli",
        "commands/plugin.py": "cli",
        "commands/dispatch.py": "cli",
        "configuration/models.py": "config",
        "configuration/layers.py": "config",
        "configuration/hosts.py": "config",
        "configuration/paths.py": "config",
        "transport/gateway.py": "runtime",
        "application/service.py": "runtime",
        "application/composer.py": "runtime",
        "application/dependencies.py": "runtime",
        "media/artifact_pipeline.py": "runtime",
        "observability/chapter_reporter.py": "runtime",
        "output/output_allocator.py": "runtime",
        "plugins/plugin_manifest.py": "security",
        "plugins/lifecycle.py": "security",
        "plugins/runtime.py": "security",
        "plugins/management.py": "security",
        "privacy/log_safety.py": "observability.logging",
    }
    for relative_path, forbidden in boundaries.items():
        imports = _relative_imports(source_root / relative_path)
        assert forbidden not in imports, (
            f"{relative_path} imports its facade {forbidden}; this creates a circular boundary"
        )
