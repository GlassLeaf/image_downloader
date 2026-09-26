"""Executable checks for the implementation-backed v3 documentation taxonomy."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import re
import sys
from dataclasses import MISSING, fields, is_dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace

from image_downloader.cli import build_parser
from image_downloader.exceptions import ERROR_CATALOG
from image_downloader.plugins.plugin_manifest import _MANIFEST_FIELDS, _validate_relaxed_manifest
from image_downloader.models import UpdateChangeKind

LINK = re.compile(r"\[[^\]]*\]\((?P<target>[^)#]+)(?:#(?P<fragment>[^)]+))?\)")
DESTINATION_LINK = re.compile(r"\[[^\]]*\]\((?P<file>[^)#]+)#(?P<anchor>[^)]+)\)")
LEDGER_ROW = re.compile(
    r"^\| (?P<id>(?:LEGACY|TAX)-[A-Z]+-\d+) \| (?P<source>.*?) \| (?P<requirement>.*?) "
    r"\| (?P<status>carried|merged|superseded) \| (?P<evidence>.*?) \| (?P<destination>.*) \|$",
    re.MULTILINE,
)
CITATION_ROW = re.compile(
    r'^\| (?P<id>(?:LEGACY|TAX)-[A-Z]+-\d+) \| (?P<source>.*?); "(?P<quote>.*?)" '
    r'\| "(?P<proof>.*?)" \|$',
    re.MULTILINE,
)
ANCHOR = re.compile(r'<a id="(?P<id>[a-z0-9-]+)"></a>')
INVENTORY = re.compile(
    r"<!-- api-inventory:start (?P<module>[a-z_.]+) -->\s*(?P<names>.*?)<!-- api-inventory:end -->",
    re.DOTALL,
)
CONTRACT_MARKER = re.compile(
    r"(?P<line>^.*<!-- api-contract: (?P<symbol>[A-Za-z_][A-Za-z0-9_.]+) -->.*$)",
    re.MULTILINE,
)
FACADE_MODULES = (
    "image_downloader.observability.logging",
    "image_downloader.config",
    "image_downloader.runtime",
    "image_downloader.security",
    "image_downloader.cli",
)


def _ordinary_class_contract_symbols() -> set[str]:
    """Return constructors and own public members requiring visible signatures.

    Dataclass/Pydantic constructor fields and Protocol hooks have deliberately
    different documentation contracts.  Dataclass members which add behavior
    beyond fields are listed explicitly below.
    """
    symbols: set[str] = set()
    for module_name in FACADE_MODULES:
        module = importlib.import_module(module_name)
        for name in module.__all__:
            value = getattr(module, name)
            if (
                not inspect.isclass(value)
                or not value.__module__.startswith("image_downloader")
                or is_dataclass(value)
                or hasattr(value, "model_fields")
                or getattr(value, "_is_protocol", False)
            ):
                continue
            base = f"{module_name}.{name}"
            symbols.add(base)
            for member_name, descriptor in value.__dict__.items():
                if member_name.startswith("_"):
                    continue
                if isinstance(descriptor, (property, classmethod, staticmethod)) or inspect.isfunction(descriptor):
                    symbols.add(f"{base}.{member_name}")
    return symbols


DTO_BEHAVIOR_SYMBOLS = {
    "image_downloader.runtime.OutputAllocation",
    "image_downloader.runtime.OutputAllocation.should_write",
    "image_downloader.runtime.OutputAllocation.commit",
    "image_downloader.runtime.OutputAllocation.abort",
    "image_downloader.security.CatalogEntry.content_pinned",
    "image_downloader.security.CatalogEntry.as_json",
    "image_downloader.security.PluginManifest.id",
    "image_downloader.security.PluginManifest.kind",
    "image_downloader.security.PluginRecord.id",
    "image_downloader.security.PluginRecord.kind",
    "image_downloader.security.PluginUninstallResult.as_json",
    "image_downloader.observability.logging.LogRecord.formatted",
}


def _command_options() -> set[str]:
    parsers = [build_parser()]
    options: set[str] = set()
    while parsers:
        parser = parsers.pop()
        for action in parser._actions:  # argparse has no public parser traversal API
            options.update(option for option in action.option_strings if option not in {"-h", "--help"})
            if isinstance(action.choices, dict):
                parsers.extend(action.choices.values())
    return options


def _read(repository_root: Path, relative: str) -> str:
    return (repository_root / relative).read_text(encoding="utf-8")


def _heading_anchor(heading: str) -> str:
    normalized = re.sub(r"[^\w\s-]", "", heading.casefold())
    return re.sub(r"\s+", "-", normalized).strip("-")


def _all_anchors(document: Path) -> set[str]:
    content = document.read_text(encoding="utf-8")
    anchors = set(ANCHOR.findall(content))
    for line in content.splitlines():
        if match := re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line):
            anchors.add(_heading_anchor(match.group(1)))
    return anchors


def test_reference_index_has_the_diataxis_entrypoints(repository_root: Path) -> None:
    index = _read(repository_root, "docs/v3/README.md")
    for directory in ("tutorials/", "how-to/", "reference/", "explanation/", "maintenance/"):
        assert directory in index
    for reference in (
        "reference/cli.md",
        "reference/configuration.md",
        "reference/runtime-behavior.md",
        "reference/plugin-package.md",
        "reference/plugin-hooks.md",
        "reference/library-api.md",
        "reference/logging.md",
    ):
        assert reference in index


def test_cli_reference_covers_every_parser_option_and_output_contract(repository_root: Path) -> None:
    reference = _read(repository_root, "docs/v3/reference/cli.md")
    for option in _command_options():
        assert option in reference, option
    for value in (
        "bypass-all",
        "bypass-catalog",
        "bypass-signature",
        "overwrite",
        "skip",
        "rename",
        "error",
        "JPEG",
        "PNG",
        "WEBP",
        "auto",
        "enabled",
        "disabled",
        "browser-import",
    ):
        assert value in reference
    for marker in ("cli-forms", "cli-options", "cli-exit-status", "cli-success-json", "cli-errors"):
        assert f'<a id="{marker}"></a>' in reference
    assert "JSON file" in reference
    assert "YAML is not accepted" in reference
    assert "present only when known" in reference


def test_configuration_plugin_and_runtime_references_cover_high_risk_semantics(
    repository_root: Path,
) -> None:
    configuration = _read(repository_root, "docs/v3/reference/configuration.md")
    package = _read(repository_root, "docs/v3/reference/plugin-package.md")
    hooks = _read(repository_root, "docs/v3/reference/plugin-hooks.md")
    runtime = _read(repository_root, "docs/v3/reference/runtime-behavior.md")
    template = _read(repository_root, "src/image_downloader/config-template.yaml")

    for key in (
        "profile.default",
        "storage.data_root",
        "plugins.root",
        "download.chapter_concurrency",
        "output.lock_timeout_seconds",
        "media.max_image_pixels",
        "logging.safe_query_parameters",
        "network.max_response_bytes",
        "notification.email.smtp_port",
        "security.plugin_verification",
        "image_processors.chain",
        "plugin_settings.<id>.secrets",
        "fallback.generic_html.enabled",
    ):
        assert key in configuration
    assert "config-template.yaml" in configuration
    assert "max_response_bytes" in template
    assert "IMAGE_DOWNLOADER_PLUGIN_" in configuration
    assert "rewrite_user_layers=True" in configuration

    for field in (
        "schema_version",
        "api_version",
        "public_key",
        "key_id",
        "file_tree",
        "file_tree_sha256",
        "plugin-metadata.json",
        "bypass-signature",
    ):
        assert field in package
    assert "exact 14-field object" in package
    assert "capabilities" in package
    assert "match_priority" in package
    assert "Relaxed manifest used by bypass modes" in package
    assert "match_priority` がなければ runtime は `0` を補完" in package
    assert "exact object" in package
    for hook in ("matches_with_config", "recover_image_request", "async aclose()", "inspect"):
        assert hook in hooks
    for topic in ("AES-256-GCM", "legacy_records", "cross-origin", "max_response_bytes"):
        assert topic in runtime


def test_plugin_package_schema_and_signer_metadata_match_implementation(repository_root: Path) -> None:
    package = _read(repository_root, "docs/v3/reference/plugin-package.md")
    template = _read(repository_root, "examples/plugin-v3-template/plugin-metadata.json.example")

    assert set(_MANIFEST_FIELDS) == {
        "schema_version",
        "id",
        "publisher",
        "version",
        "api_version",
        "kind",
        "capabilities",
        "match_priority",
        "entry",
        "config_file",
        "public_key",
        "key_id",
        "file_tree",
        "file_tree_sha256",
    }
    for field in _MANIFEST_FIELDS:
        assert f"`{field}`" in package
    for field in ("id", "publisher", "version", "kind", "capabilities", "match_priority", "entry", "config_file"):
        assert f'"{field}"' in template

    relaxed = _validate_relaxed_manifest(
        {"manifest": {"id": "com.example.relaxed", "kind": "site_plugin", "entry": {"file": "plugin.py", "class": "Plugin"}}},
        Path("unit"),
    )
    assert relaxed.value["match_priority"] == 0


def test_reference_documents_state_the_correct_validation_and_lifecycle_boundaries(
    repository_root: Path,
) -> None:
    cli = _read(repository_root, "docs/v3/reference/cli.md")
    library = _read(repository_root, "docs/v3/reference/library-api.md")
    hooks = _read(repository_root, "docs/v3/reference/plugin-hooks.md")
    lifecycle = _read(repository_root, "docs/v3/explanation/execution-lifecycle.md")
    tutorial = _read(repository_root, "docs/v3/tutorials/embedded-download.md")

    assert "does apply" in cli
    assert "does not alter the catalog-entry" in cli
    assert "constructor 自体は URL を検査しない" in library
    assert "syntactically valid な dummy URL" in hooks
    assert "transport failure には recovery hook は呼ばれない" in lifecycle
    assert "HTTP response の status が `>=400`" in lifecycle
    assert "skip allocation" in library
    assert 'ADDED="added"' in library
    assert "from image_downloader.config import plugin_root" in tutorial
    assert "plugin_root=plugin_root(config)" in tutorial
    assert "Path(config.plugins.root)" not in tutorial

    assert UpdateChangeKind.ADDED.value == "added"
    assert UpdateChangeKind.CHANGED.value == "changed"
    assert UpdateChangeKind.REMOVED.value == "removed"


def test_cli_plugin_and_tutorial_references_expose_actionable_contracts(repository_root: Path) -> None:
    cli = _read(repository_root, "docs/v3/reference/cli.md")
    configuration = _read(repository_root, "docs/v3/reference/configuration.md")
    package = _read(repository_root, "docs/v3/reference/plugin-package.md")
    hooks = _read(repository_root, "docs/v3/reference/plugin-hooks.md")
    tutorial = _read(repository_root, "docs/v3/tutorials/embedded-download.md")

    for marker in ("cli-io", "cli-cookie-actions", "cli-examples", "cli-doctor-json"):
        assert f'<a id="{marker}"></a>' in cli
    for phrase in (
        "cookie export PATH",
        "cookie import PATH",
        "cookie browser-import DOMAIN",
        "does not download images",
        "loaded_plugins[]",
        "Plugin Python `print()` is redirected to stderr",
    ):
        assert phrase in cli
    assert '<a id="config-storage-layout"></a>' in configuration
    for phrase in ("%NUM%", "%TITLE%", "%SUBTITLE%", "%EXT%", "profiles/<profile>", "cookie/cookies.enc"):
        assert phrase in configuration
    assert "filename は固定ではない" in package
    assert "bypass-signature" in package
    assert '<a id="plugin-signing-workflow"></a>' in package
    for marker in ("plugin-contexts", "plugin-hook-errors"):
        assert f'<a id="{marker}"></a>' in hooks
    for phrase in ("401 and 403", "TransformContext", "CancelledError", "ordinary `Exception`"):
        assert phrase in hooks
    for heading in (
        "Check a complete update snapshot",
        "Use a one-operation plugin override",
        "Registry-only inspection",
        "Handle result failures and operation failures separately",
        "Advanced dependency injection",
    ):
        assert heading in tutorial
    python_blocks = re.findall(r"```python\n(.*?)\n```", tutorial, flags=re.DOTALL)
    assert len(python_blocks) >= 5
    for number, block in enumerate(python_blocks, start=1):
        compile(block, f"embedded-download-block-{number}", "exec")


def test_embedded_tutorial_snippets_execute_in_an_isolated_facade(
    monkeypatch, repository_root: Path, tmp_path: Path
) -> None:
    """Exercise the published examples without a real config, plugin, or network.

    The separate public-API tests exercise the real composer with temporary
    roots.  This test ensures every tutorial block is executable and calls the
    stable API with the documented shape, while deliberately keeping examples
    from writing a user's configuration, cookies, plugins, or logs.
    """
    tutorial = _read(repository_root, "docs/v3/tutorials/embedded-download.md")
    blocks = re.findall(r"```python\n(.*?)\n```", tutorial, flags=re.DOTALL)
    package = ModuleType("image_downloader")
    config_module = ModuleType("image_downloader.config")
    created_composers: list[object] = []

    class FakeService:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

        async def run(self, url: str, **kwargs):
            self.url = url
            self.kwargs = kwargs
            outcome = SimpleNamespace(image=SimpleNamespace(image_id="image-1"), failure=None)
            return SimpleNamespace(chapters=(SimpleNamespace(outcomes=(outcome,)),))

        async def check_updates(self, url: str, **kwargs):
            self.url = url
            self.kwargs = kwargs
            return SimpleNamespace(
                changes=(
                    SimpleNamespace(url="https://example.test/changed", kind=SimpleNamespace(value="changed")),
                    SimpleNamespace(url="https://example.test/removed", kind=SimpleNamespace(value="removed")),
                )
            )

    class FakeRegistry:
        closed = False

        def doctor_validate(self) -> None:
            return None

        def resolve(self, url: str, *, fallback_enabled: bool) -> None:
            self.url = url
            self.fallback_enabled = fallback_enabled

        def close(self) -> None:
            self.closed = True

    class FakeComposer:
        def __init__(self, config, *, config_root: Path, plugin_root: Path) -> None:
            self.config = config
            self.config_root = config_root
            self.plugin_root = plugin_root
            self.service = FakeService()
            self.registry = FakeRegistry()
            created_composers.append(self)

        def compose(self) -> FakeService:
            return self.service

        def compose_registry(self) -> FakeRegistry:
            return self.registry

    class FakeDownloadService:
        def __init__(self, config, registry, dependencies) -> None:
            self.config = config
            self.registry = registry
            self.dependencies = dependencies

    class FakeImageDownloaderError(Exception):
        pass

    package.RuntimeComposer = FakeComposer
    package.DownloadService = FakeDownloadService
    package.load_application_config = lambda path, **kwargs: SimpleNamespace()
    package.AuthenticationError = FakeImageDownloaderError
    package.ImageDownloaderError = FakeImageDownloaderError
    package.PluginError = FakeImageDownloaderError
    config_module.plugin_root = lambda config: tmp_path / "platform" / "plugins"
    monkeypatch.setitem(sys.modules, "image_downloader", package)
    monkeypatch.setitem(sys.modules, "image_downloader.config", config_module)
    monkeypatch.chdir(tmp_path)

    namespace: dict[str, object] = {"__name__": "documentation_example"}
    for number, block in enumerate(blocks, start=1):
        exec(compile(block, f"embedded-download-block-{number}", "exec"), namespace)

    asyncio.run(namespace["download"]())
    changes = asyncio.run(namespace["check_for_updates"](SimpleNamespace(), tmp_path / "config"))
    assert changes == ["https://example.test/changed"]
    asyncio.run(namespace["download_with_override"](SimpleNamespace(), tmp_path / "config"))
    namespace["inspect_registry"](SimpleNamespace(), tmp_path / "config")
    asyncio.run(namespace["download_with_handling"](SimpleNamespace(), tmp_path / "config"))
    injected = object()
    service = namespace["build_service"](SimpleNamespace(), object(), injected)

    assert service.dependencies is injected
    inspected_registries = [
        composer.registry for composer in created_composers if hasattr(composer.registry, "url")
    ]
    assert len(inspected_registries) == 1
    assert inspected_registries[0].closed
    assert not (tmp_path / "platform" / "plugins").exists()


def test_facade_exports_and_error_catalog_match_the_visible_reference(repository_root: Path) -> None:
    inventory = _read(repository_root, "docs/v3/reference/api-contract-inventory.md")
    documented = {
        match.group("module"): tuple(
            line.strip() for line in match.group("names").splitlines() if line.strip()
        )
        for match in INVENTORY.finditer(inventory)
    }
    for module_name, names in documented.items():
        assert tuple(importlib.import_module(module_name).__all__) == names

    library = _read(repository_root, "docs/v3/reference/library-api.md")
    for entry in ERROR_CATALOG:
        assert (
            f"| `{entry.exception_name}` | `{entry.code}` | {entry.reason} |" in library
        )
        for attribute in entry.attributes:
            assert f"`{attribute}`" in library
    for detail in ("PluginConfigOverrides", "_RuntimeDependencies", "CancelledError", "ImageOutcome"):
        assert detail in library


def _resolve_contract_symbol(symbol: str) -> object:
    for module_name in FACADE_MODULES:
        prefix = f"{module_name}."
        if symbol.startswith(prefix):
            value: object = importlib.import_module(module_name)
            for attribute in symbol.removeprefix(prefix).split("."):
                value = getattr(value, attribute)
            return value
    raise AssertionError(f"unknown facade marker: {symbol}")


def _normalise_annotation(value: object) -> str:
    """Make reflection spelling comparable with a readable Markdown signature."""
    text = str(value).replace("typing.", "").replace("collections.abc.", "")
    text = re.sub(r"<class '([^']+)'>", r"\1", text)
    text = re.sub(r"['\"]([A-Za-z_][A-Za-z0-9_.]*)['\"]", r"\1", text)
    return text.replace("NoneType", "None")


def test_visible_signature_markers_match_runtime_callability(repository_root: Path) -> None:
    signatures = _read(repository_root, "docs/v3/reference/api-signatures.md")
    markers = list(CONTRACT_MARKER.finditer(signatures))
    documented_symbols = {marker.group("symbol") for marker in markers}

    assert len(markers) >= 100
    assert len(documented_symbols) == len(markers)
    assert _ordinary_class_contract_symbols() <= documented_symbols
    assert DTO_BEHAVIOR_SYMBOLS <= documented_symbols
    for marker in markers:
        value = _resolve_contract_symbol(marker.group("symbol"))
        line = marker.group("line")
        property_getter = value.fget if isinstance(value, property) else None
        callable_value = property_getter if property_getter is not None else value
        assert callable(callable_value), marker.group("symbol")
        comparable_line = _normalise_annotation(line)
        assert inspect.iscoroutinefunction(callable_value) == bool(re.search(r"`async\s", line)), marker.group("symbol")
        signature = inspect.signature(callable_value)
        if signature.return_annotation is not inspect.Signature.empty and not inspect.isclass(value):
            assert "->" in line, marker.group("symbol")
            assert _normalise_annotation(signature.return_annotation) in comparable_line, marker.group("symbol")
        for parameter in signature.parameters.values():
            assert parameter.name in line, (marker.group("symbol"), parameter.name)
            if parameter.annotation is not inspect.Parameter.empty:
                assert f"{parameter.name}:" in line, (marker.group("symbol"), parameter.name)
                assert _normalise_annotation(parameter.annotation) in comparable_line, (
                    marker.group("symbol"),
                    parameter.name,
                    parameter.annotation,
                )
            if parameter.kind is inspect.Parameter.KEYWORD_ONLY:
                assert "*" in line, marker.group("symbol")
            if parameter.default is not inspect.Parameter.empty:
                value_text = str(parameter.default)
                assert re.search(
                    rf"{re.escape(parameter.name)}(?::[^=]+)?\s*=\s*['\"]?{re.escape(value_text)}['\"]?",
                    line,
                ), (marker.group("symbol"), parameter.name, value_text)


def test_dataclass_and_configuration_model_fields_have_a_visible_reference(repository_root: Path) -> None:
    signatures = _read(repository_root, "docs/v3/reference/api-signatures.md")
    configuration = _read(repository_root, "docs/v3/reference/configuration.md")
    template = _read(repository_root, "src/image_downloader/config-template.yaml")

    for module_name in (
        "image_downloader",
        "image_downloader.runtime",
        "image_downloader.security",
        "image_downloader.observability.logging",
    ):
        module = importlib.import_module(module_name)
        for name in module.__all__:
            value = getattr(module, name)
            if not inspect.isclass(value) or not is_dataclass(value):
                continue
            lines = [line for line in signatures.splitlines() if f"`{name}`" in line]
            assert lines, name
            row = " ".join(lines)
            for field in fields(value):
                assert field.name in row, (name, field.name)
                if field.default_factory is not MISSING:  # type: ignore[comparison-overlap]
                    assert "<factory:" in row, (name, field.name)

    config_module = importlib.import_module("image_downloader.config")
    for name in config_module.__all__:
        value = getattr(config_module, name)
        if not inspect.isclass(value):
            continue
        model_fields = getattr(value, "model_fields", None)
        if not isinstance(model_fields, dict):
            continue
        for field_name, field_info in model_fields.items():
            # Pydantic uses `from_` as the Python-safe attribute for the YAML
            # key `from`.  Documentation and the template correctly expose
            # the accepted external alias, not the implementation identifier.
            external_name = field_info.validation_alias or field_info.alias or field_name
            assert external_name in configuration, (name, external_name)
            assert external_name in template, (name, external_name)


def test_coverage_ledger_has_source_evidence_and_live_destinations(repository_root: Path) -> None:
    ledger_path = repository_root / "docs" / "v3" / "maintenance" / "legacy-coverage.md"
    ledger = ledger_path.read_text(encoding="utf-8")
    rows = list(LEDGER_ROW.finditer(ledger))

    assert len(rows) >= 75
    assert len({row.group("id") for row in rows}) == len(rows)
    assert any(row.group("id").startswith("TAX-") for row in rows)
    assert (repository_root / "archive" / "docs" / "legacy" / "v3-pre-reorg").is_dir()
    assert (repository_root / "archive" / "docs" / "legacy" / "v3-pre-taxonomy").is_dir()
    for row in rows:
        assert row.group("source")
        assert row.group("requirement")
        assert row.group("evidence")
        source_locator = row.group("source").split(";", 1)[0]
        if source_locator.startswith("pre-reorg/"):
            source = (
                repository_root
                / "archive"
                / "docs"
                / "legacy"
                / "v3-pre-reorg"
                / source_locator.removeprefix("pre-reorg/")
            )
            assert source.is_file(), row.group("id")
        if source_locator.startswith("pre-taxonomy/"):
            source = (
                repository_root
                / "archive"
                / "docs"
                / "legacy"
                / "v3-pre-taxonomy"
                / "corpus"
                / "docs"
                / "v3"
                / source_locator.removeprefix("pre-taxonomy/")
            )
            assert source.is_file(), row.group("id")
        destination = row.group("destination")
        match = DESTINATION_LINK.search(destination)
        assert match, row.group("id")
        destination_document = (ledger_path.parent / match.group("file")).resolve()
        assert destination_document.is_file(), row.group("id")
        anchors = ANCHOR.findall(destination_document.read_text(encoding="utf-8"))
        assert match.group("anchor") in anchors, row.group("id")
        if row.group("status") == "superseded":
            assert "see [" in destination or "The " in destination or "Dispatcher" in destination


def test_coverage_ledger_quotes_snapshots_and_keeps_visible_destination_proof(
    repository_root: Path,
) -> None:
    ledger_path = repository_root / "docs" / "v3" / "maintenance" / "legacy-coverage.md"
    ledger = ledger_path.read_text(encoding="utf-8")
    claims = {row.group("id"): row for row in LEDGER_ROW.finditer(ledger)}
    citations = {row.group("id"): row for row in CITATION_ROW.finditer(ledger)}

    assert citations.keys() == claims.keys()
    for claim_id, citation in citations.items():
        locator = citation.group("source")
        if locator.startswith("pre-reorg/"):
            source = repository_root / "archive" / "docs" / "legacy" / "v3-pre-reorg" / locator.removeprefix("pre-reorg/")
        else:
            assert locator.startswith("pre-taxonomy/"), claim_id
            source = (
                repository_root
                / "archive"
                / "docs"
                / "legacy"
                / "v3-pre-taxonomy"
                / "corpus"
                / "docs"
                / "v3"
                / locator.removeprefix("pre-taxonomy/")
            )
        source_text = source.read_text(encoding="utf-8")
        assert citation.group("quote") in source_text, claim_id

        destination = DESTINATION_LINK.search(claims[claim_id].group("destination"))
        assert destination is not None, claim_id
        destination_text = (ledger_path.parent / destination.group("file")).resolve().read_text(encoding="utf-8")
        proof = citation.group("proof").replace("&lbrack;", "[").replace("&rbrack;", "]")
        assert proof in destination_text, claim_id


def test_current_documentation_links_are_recursive_and_archive_is_excluded(repository_root: Path) -> None:
    documents = (
        repository_root / "README.md",
        repository_root / "docs" / "README.md",
        *(repository_root / "docs" / "v3").rglob("*.md"),
        repository_root / "plugin-sources" / "README.md",
        repository_root / "examples" / "plugin-v3-template" / "README.md",
    )
    for document in documents:
        for match in LINK.finditer(document.read_text(encoding="utf-8")):
            target = match.group("target")
            if target.startswith(("http:", "https:", "mailto:")):
                continue
            destination = document.parent / target
            assert destination.exists(), f"broken link in {document}: {target}"
            if fragment := match.group("fragment"):
                assert fragment in _all_anchors(destination), (
                    f"broken anchor in {document}: {target}#{fragment}"
                )
