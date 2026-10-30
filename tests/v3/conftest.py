"""Shared repository locations for the current v3 test suite."""

import sys
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture(autouse=True)
def forbid_real_browser_and_keyring(monkeypatch, tmp_path_factory):
    """Tests must opt into artificial providers, never host browser/OS secrets."""

    def blocked(*args, **kwargs):
        pytest.fail("Host browser/keyring access is forbidden in automated tests", pytrace=False)

    browser = ModuleType("browser_cookie3")
    browser.load = blocked
    browser.chrome = blocked
    keyring = ModuleType("keyring")
    keyring.get_password = blocked
    keyring.set_password = blocked
    keyring.delete_password = blocked
    monkeypatch.setitem(sys.modules, "browser_cookie3", browser)
    monkeypatch.setitem(sys.modules, "keyring", keyring)

    # Also block existing encrypted host cookie files BEFORE reading their bytes.
    from image_downloader.storage.cookies import CookieStore

    original = CookieStore._load_unlocked
    original_lock = CookieStore._lock
    temporary_root = tmp_path_factory.getbasetemp().resolve()
    runtime_root = Path(__file__).resolve().parents[2] / ".runtime"

    def require_artificial(path):
        root = path.resolve()
        if not root.is_relative_to(temporary_root) and not root.is_relative_to(runtime_root):
            pytest.fail("Host cookie files are forbidden in automated tests", pytrace=False)

    def load_artificial(store):
        require_artificial(store.filesystem.root)
        return original(store)

    def lock_artificial(store):
        require_artificial(store.filesystem.root)
        return original_lock(store)

    from image_downloader.storage import browser_cookies

    original_chrome = browser_cookies.import_chrome

    def chrome_artificial(domain, root):
        if root.is_absolute():
            require_artificial(root)
        return original_chrome(domain, root)

    monkeypatch.setattr(CookieStore, "_load_unlocked", load_artificial)
    monkeypatch.setattr(CookieStore, "_lock", lock_artificial)
    monkeypatch.setattr(browser_cookies, "import_chrome", chrome_artificial)


@pytest.fixture(scope="session")
def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def plugin_sources(repository_root: Path) -> Path:
    return repository_root / "plugin-sources"


@pytest.fixture(scope="session")
def local_plugin_fixtures() -> Path:
    return Path(__file__).resolve().parent / "fixtures" / "local_plugins"
