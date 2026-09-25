from __future__ import annotations

import re
from pathlib import Path

from image_downloader import (
    AppConfig,
    ExistingFileConflictError,
    StorageError,
    apply_overrides,
    load_application_config,
    resolve_application_config,
)
from image_downloader.cli import build_parser
from image_downloader.exceptions import error_catalog_markdown
from image_downloader.observability.logging import mask_log_text
from image_downloader.storage import FileSystem
from image_downloader.storage.cookies import CookieStore

LINK = re.compile(r"\[[^\]]*\]\((?P<target>[^)#]+)(?:#[^)]+)?\)")


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

    assert isinstance(error, StorageError)
    assert error.code == "existing_file_conflict"
    assert error.relative_path == Path("chapter/0001.jpeg")
    assert error.policy == "error"


def test_cookie_store_requires_the_v3_filesystem_boundary(tmp_path: Path) -> None:
    store = CookieStore(FileSystem((tmp_path / "cookie").resolve()))

    assert store.path.name == "cookies.enc"
    assert list(store.load()) == []


def test_current_cli_and_logging_contracts_are_documented_v3_features(tmp_path: Path) -> None:
    args = build_parser().parse_args(["--export-cookies", str(tmp_path / "cookies.export")])

    assert args.export_cookies == tmp_path / "cookies.export"
    assert "secret=[REDACTED]" in mask_log_text("secret=value")


def test_current_documentation_links_and_root_import_example_are_valid(repository_root: Path) -> None:
    documents = (
        repository_root / "README.md",
        repository_root / "docs" / "README.md",
        repository_root / "docs" / "plugin-api-v3.md",
        *(repository_root / "docs" / "v3").glob("*.md"),
        *(repository_root / "archive" / "docs" / "legacy").rglob("*.md"),
        repository_root / "plugin-sources" / "README.md",
        repository_root / "examples" / "plugin-v3-template" / "README.md",
        repository_root / "examples" / "legacy" / "v2" / "README.md",
    )
    assert not (repository_root / "docs" / "legacy").exists()
    assert (repository_root / "archive" / "docs" / "legacy" / "v1" / "README.md").is_file()
    assert (repository_root / "archive" / "docs" / "legacy" / "v2" / "README.md").is_file()
    for document in documents:
        for match in LINK.finditer(document.read_text(encoding="utf-8")):
            target = match.group("target")
            if target.startswith(("http:", "https:", "mailto:")):
                continue
            assert (document.parent / target).exists(), f"broken link in {document}: {target}"

    library_api = (repository_root / "docs" / "v3" / "library-api.md").read_text(encoding="utf-8")
    assert "from image_downloader import RuntimeComposer, load_application_config" in library_api
    assert "from image_downloader.config import" not in library_api
    integration_guide = (repository_root / "docs" / "v3" / "site-plugin-integration-guide.md").read_text(
        encoding="utf-8"
    )
    for required_topic in (
        "RequestSpec",
        "AuthFlow",
        "cookie browser-import",
        "network.origin_request_concurrency",
        "UnsupportedSiteFeature",
    ):
        assert required_topic in integration_guide
    start = "<!-- error-catalog:start -->"
    end = "<!-- error-catalog:end -->"
    documented_catalog = library_api.split(start, 1)[1].split(end, 1)[0].strip()
    assert documented_catalog == error_catalog_markdown()
