"""Import only cookies applicable to the requested host, within the packaged PSL."""

from __future__ import annotations

import json
import sys
from http.cookiejar import CookieJar
from pathlib import Path
from types import SimpleNamespace

import pytest
from support.vault import cookie_store

from image_downloader.storage.browser_cookies import _load
from image_downloader.storage.cookie_selection import BrowserCookieSelection
from image_downloader.storage.cookies import _cookie


@pytest.mark.parametrize(
    "host,domain,domain_cookie,expected",
    [
        ("gallery.example.com", ".example.com", True, True),
        ("gallery.example.com", "example.com", True, True),
        ("gallery.example.com", "example.com", False, False),
        ("gallery.example.com", "gallery.example.com", False, True),
        ("gallery.example.com", ".gallery.example.com", True, True),
        ("gallery.example.com", ".sibling.example.com", True, False),
        ("gallery.example.com", ".child.gallery.example.com", True, False),
        ("gallery.example.com", ".example.com.attacker", True, False),
        ("gallery.example.com", ".ample.com", True, False),
        ("gallery.example.com", ".com", True, False),
        ("images.tenant.github.io", ".tenant.github.io", True, True),
        ("images.tenant.github.io", ".github.io", True, False),
        ("images.tenant.github.io", ".other.github.io", True, False),
        ("images.example.co.uk", ".example.co.uk", True, True),
        ("images.example.co.uk", ".co.uk", True, False),
        ("gallery.example.test", ".example.test", True, False),
        ("gallery.example.test", ".gallery.example.test", True, True),
        ("127.0.0.1", "127.0.0.1", False, True),
        ("127.0.0.1", ".127.0.0.1", True, True),
        ("127.0.0.1", "0.0.1", True, False),
        ("::1", "::1", False, True),
        ("Gallery.BÜCHER.de.", ".xn--bcher-kva.de", True, True),
    ],
)
def test_domain_cookie_and_host_only_scopes(host, domain, domain_cookie, expected):
    cookie = _cookie("session", "artificial", domain=domain, domain_specified=domain_cookie)
    before = vars(cookie).copy()
    assert BrowserCookieSelection.for_host(host).accepts(cookie) is expected
    assert vars(cookie) == before


@pytest.mark.parametrize("explicit", [False, True])
def test_loader_fetches_parent_candidates_then_merges_only_applicable_cookies(tmp_path, monkeypatch, explicit):
    cookies = [
        _cookie("parent", "artificial", domain=".example.com", domain_specified=True, secure=True),
        _cookie("host", "artificial", domain="gallery.example.com"),
        _cookie("parent_host_only", "unrelated", domain="example.com"),
        _cookie("sibling", "unrelated", domain="other.example.com"),
        _cookie("attacker", "unrelated", domain="gallery.example.com.attacker"),
    ]
    calls = []

    def loader(**kwargs):
        calls.append(kwargs)
        result = CookieJar()
        for cookie in cookies:
            if kwargs["domain_name"] in cookie.domain:
                result.set_cookie(cookie)
        return result

    monkeypatch.setitem(sys.modules, "browser_cookie3", SimpleNamespace(chrome=loader, load=loader))
    source = tmp_path / "artificial-chrome"
    (source / "Default/Network").mkdir(parents=True)
    (source / "Default/Network/Cookies").write_bytes(b"SQLite format 3\0artificial-db")
    (source / "Local State").write_text(json.dumps({"os_crypt": {"encrypted_key": "artificial-key"}}))
    store = cookie_store(tmp_path / "target")
    existing = CookieJar()
    existing.set_cookie(_cookie("existing", "keep", domain="other.test"))
    store.save(existing)
    store.import_browser("Gallery.Example.COM.", chrome_user_data_dir=source if explicit else None)
    loaded = {cookie.name: cookie for cookie in store.load()}
    assert set(loaded) == {"parent", "host", "existing"}
    assert loaded["parent"].domain == ".example.com"
    assert loaded["parent"].secure is True
    assert loaded["host"].domain_specified is False
    assert [call["domain_name"] for call in calls] == ["example.com"]


def test_explicit_loader_fails_closed_when_psl_cannot_determine_parent(monkeypatch):
    def loader(**kwargs):
        assert kwargs["domain_name"] == "gallery.example.test"
        jar = CookieJar()
        jar.set_cookie(_cookie("parent", "artificial", domain=".example.test", domain_specified=True))
        jar.set_cookie(_cookie("host", "artificial", domain="gallery.example.test"))
        return jar

    monkeypatch.setitem(sys.modules, "browser_cookie3", SimpleNamespace(chrome=loader))
    assert [cookie.name for cookie in _load("gallery.example.test", Path("fake-db"), Path("fake-state"))] == ["host"]
