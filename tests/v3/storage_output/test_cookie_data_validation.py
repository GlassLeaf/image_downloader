"""Reject malformed encrypted Cookie data without replacing persisted bytes."""

from __future__ import annotations

import json
import sys
from http.cookiejar import CookieJar
from types import SimpleNamespace

import httpx
import pytest

from image_downloader import AuthenticationError, StorageSafetyError
from image_downloader.exceptions import error_info_for
from image_downloader.storage import FileSystem, cookies

PASSPHRASE = "fictional-passphrase-canary"
RECORD = {"name": "session", "value": "fictional-cookie-canary", "domain": "example.test"}


@pytest.fixture
def store(tmp_path, monkeypatch):
    key = b"k" * 32
    monkeypatch.setattr(cookies.CookieStore, "_key", lambda self, *, create: key)
    monkeypatch.setattr(cookies, "_derive", lambda passphrase, salt: key)
    result = cookies.CookieStore(FileSystem((tmp_path / "profile").resolve()))
    result.save(cookies._deserialize(json.dumps([RECORD])))
    return result


def envelope(payload):
    return cookies._encrypt(payload, b"k" * 32)


def export(value):
    if isinstance(value, dict):
        return dict(value, kind="image-downloader-cookie-export", kdf="scrypt", salt=cookies._b64(b"s" * 16))
    return value


def assert_safe(error, message):
    assert type(error) is AuthenticationError
    assert str(error) == message
    assert error.__cause__ is not None
    diagnostic = repr(error_info_for(error))
    for secret in (PASSPHRASE, RECORD["value"], str(b"k" * 32)):
        assert secret not in str(error) and secret not in diagnostic


CONTAINER_MUTATIONS = [
    ("format", None),
    ("format", True),
    ("format", 1.0),
    ("format", "1"),
    ("format", 2),
    ("cipher", None),
    ("cipher", "other"),
    ("cipher", []),
    ("nonce", None),
    ("nonce", 42),
    ("nonce", "!!!"),
    ("nonce", "é"),
    ("nonce", cookies._b64(b"n" * 11)),
    ("nonce", cookies._b64(b"n" * 13)),
    ("ciphertext", None),
    ("ciphertext", {}),
    ("ciphertext", "!!!"),
    ("ciphertext", cookies._b64(b"c" * 15)),
    ("ciphertext", cookies._b64(b"c" * 16)),
]


@pytest.mark.parametrize("mode", ["load", "import"])
@pytest.mark.parametrize(
    "case",
    [
        "array",
        "null",
        "string",
        "number",
        "empty-object",
        "missing-format",
        "missing-cipher",
        "missing-nonce",
        "missing-ciphertext",
        *range(len(CONTAINER_MUTATIONS)),
    ],
)
def test_invalid_container_maps_to_authentication_error_and_preserves_file(store, tmp_path, monkeypatch, mode, case):
    value = envelope(json.dumps([RECORD]).encode())
    roots = {"array": [], "null": None, "string": "fictional-cookie-canary", "number": 42, "empty-object": {}}
    if isinstance(case, int):
        field, invalid = CONTAINER_MUTATIONS[case]
        value[field] = invalid
    elif case.startswith("missing-"):
        del value[case.removeprefix("missing-")]
    else:
        value = roots[case]
    if mode == "load":
        store.path.write_text(json.dumps(value), encoding="utf-8")
        before = store.path.read_bytes()
        with pytest.raises(AuthenticationError) as caught:
            store.load()
        assert_safe(caught.value, "encrypted cookie file cannot be decrypted")
    else:
        source = tmp_path / "import.json"
        source.write_text(json.dumps(export(value)), encoding="utf-8")
        before = store.path.read_bytes()
        monkeypatch.setattr(
            store, "_lock", lambda: pytest.fail("invalid import must be validated before target locking")
        )
        with pytest.raises(AuthenticationError) as caught:
            store.import_file(source, PASSPHRASE)
        assert_safe(caught.value, "cookie import cannot be decrypted")
    assert store.path.read_bytes() == before


BAD_RECORDS = [
    {},
    {"name": "session", "domain": "example.test"},
    *[
        dict(RECORD, **{field: value})
        for field, value in (
            ("name", None),
            ("name", 1),
            ("domain", None),
            ("domain", []),
            ("value", 1),
            ("value", {}),
            ("path", None),
            ("path", 1),
            ("secure", 1),
            ("secure", "false"),
            ("domain_specified", None),
            ("domain_initial_dot", 0),
            ("expires", True),
            ("expires", "123"),
            ("expires", 1.5),
            ("expires", 10**400),
            ("expires", -(10**400)),
            ("expires", float("nan")),
            ("rest", None),
            ("rest", []),
            ("rest", {"unknown": float("nan")}),
            ("rest", {"unknown": [float("inf")]}),
            ("name", "canary-\ud800"),
            ("value", "canary-\ud800"),
            ("domain", "canary-\ud800"),
            ("path", "canary-\ud800"),
            ("rest", {"key": "canary-\ud800"}),
            ("rest", {"canary-\ud800": "value"}),
        )
    ],
]


@pytest.mark.parametrize("mode", ["load", "import"])
@pytest.mark.parametrize(
    "payload",
    [
        b"{}",
        b"null",
        b"[1]",
        b"[{}]",
        b"\xff",
        b"not-json",
        b"[" * 2000 + b"]" * 2000,
        *[json.dumps([RECORD, record]).encode() for record in BAD_RECORDS],
    ],
    ids=lambda value: "malformed-payload",
)
def test_invalid_authenticated_payload_is_not_partially_imported(store, tmp_path, monkeypatch, mode, payload):
    value = envelope(payload)
    if mode == "load":
        store.path.write_text(json.dumps(value), encoding="utf-8")
        before = store.path.read_bytes()
        with pytest.raises(AuthenticationError) as caught:
            store.load()
        assert_safe(caught.value, "encrypted cookie file cannot be decrypted")
    else:
        source = tmp_path / "import.json"
        source.write_text(json.dumps(export(value)), encoding="utf-8")
        before = store.path.read_bytes()
        monkeypatch.setattr(store, "_lock", lambda: pytest.fail("entire source must be valid before locking"))
        with pytest.raises(AuthenticationError) as caught:
            store.import_file(source, PASSPHRASE)
        assert_safe(caught.value, "cookie import cannot be decrypted")
    assert store.path.read_bytes() == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("kind", None),
        ("kind", "other"),
        ("kdf", None),
        ("kdf", "other"),
        ("salt", None),
        ("salt", 1),
        ("salt", "!!!"),
        ("salt", cookies._b64(b"s" * 15)),
        ("salt", cookies._b64(b"s" * 17)),
    ],
)
def test_export_metadata_is_validated_before_target_access(store, tmp_path, monkeypatch, field, value):
    source = tmp_path / "import.json"
    container = export(envelope(b"[]"))
    container[field] = value
    source.write_text(json.dumps(container), encoding="utf-8")
    before = store.path.read_bytes()
    monkeypatch.setattr(store, "_lock", lambda: pytest.fail("invalid export must not lock target"))
    with pytest.raises(AuthenticationError) as caught:
        store.import_file(source, PASSPHRASE)
    assert_safe(caught.value, "cookie import cannot be decrypted")
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("source_bytes", [b"not-json", b"\xff", b"[" * 2000 + b"]" * 2000])
def test_invalid_export_text_keeps_existing_file(store, tmp_path, source_bytes):
    source = tmp_path / "import.json"
    source.write_bytes(source_bytes)
    before = store.path.read_bytes()
    with pytest.raises(AuthenticationError) as caught:
        store.import_file(source, PASSPHRASE)
    assert_safe(caught.value, "cookie import cannot be decrypted")
    assert store.path.read_bytes() == before


def test_normal_httpx_cookies_keep_valueless_empty_and_rest_attributes(store):
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers=[
                    ("set-cookie", "flag"),
                    ("set-cookie", "empty="),
                    ("set-cookie", "session=value; HttpOnly; SameSite=Lax"),
                ],
            )
        )
    )
    with client:
        client.get("https://example.test/")
        store.save(client.cookies.jar)
    loaded = {cookie.name: cookie for cookie in store.load()}
    assert loaded["flag"].value is None
    assert loaded["empty"].value == ""
    assert loaded["session"]._rest == {"HttpOnly": None, "SameSite": "Lax"}
    assert {cookie.name: cookie.value for cookie in store.clone_jar(store.load())} == {
        "flag": None,
        "empty": "",
        "session": "value",
    }


def test_minimal_old_records_defaults_unknown_fields_and_json_rest(store):
    records = [
        dict(RECORD, future="ignored", rest={"HttpOnly": None, "nested": [True, 2, 1.5, {"x": "y"}]}),
        dict(RECORD, name="valueless", value=None),
        dict(RECORD, name="expired", expires=0),
        dict(RECORD, name="past", expires=-1),
    ]
    store.path.write_text(json.dumps(dict(envelope(json.dumps(records).encode()), future="ignored")), encoding="utf-8")
    loaded = {cookie.name: cookie for cookie in store.load()}
    assert set(loaded) == {"session", "valueless"}
    item = loaded["session"]
    assert item.path == "/" and not item.secure and not item.domain_specified and not item.domain_initial_dot
    assert item.expires is None and item._rest == records[0]["rest"]
    assert loaded["valueless"].value is None


@pytest.mark.parametrize("bad_target", [[], {}, {"format": True}, "malformed-record"])
def test_explicit_import_repairs_structurally_invalid_existing_container(store, tmp_path, bad_target):
    if bad_target == "malformed-record":
        bad_target = envelope(b"[{}]")
    store.path.write_text(json.dumps(bad_target), encoding="utf-8")
    source = tmp_path / "import.json"
    source.write_text(json.dumps(export(envelope(json.dumps([RECORD]).encode()))), encoding="utf-8")
    store.import_file(source, PASSPHRASE)
    assert [(cookie.name, cookie.value) for cookie in store.load()] == [("session", RECORD["value"])]


def test_browser_import_preserves_structurally_invalid_target(store, monkeypatch):
    store.path.write_text(json.dumps(envelope(b"[{}]")), encoding="utf-8")
    before = store.path.read_bytes()
    monkeypatch.setitem(sys.modules, "browser_cookie3", SimpleNamespace(load=lambda **kwargs: CookieJar()))
    with pytest.raises(AuthenticationError):
        store.import_browser("example.test")
    assert store.path.read_bytes() == before


def test_failed_atomic_save_preserves_file_and_releases_lock(store, tmp_path, monkeypatch):
    source = tmp_path / "import.json"
    source.write_text(json.dumps(export(envelope(json.dumps([dict(RECORD, name="new")]).encode()))), encoding="utf-8")
    before = store.path.read_bytes()
    with monkeypatch.context() as patch:

        def fail(*args, **kwargs):
            raise OSError("fictional-cookie-canary")

        patch.setattr(store.filesystem, "write_bytes_atomic", fail)
        with pytest.raises(AuthenticationError) as caught:
            store.import_file(source, PASSPHRASE)
        assert_safe(caught.value, "could not persist encrypted cookie file")
    assert store.path.read_bytes() == before
    store.import_file(source, PASSPHRASE)
    assert {cookie.name for cookie in store.load()} == {"session", "new"}


def test_filesystem_safety_failure_is_not_treated_as_corrupt_cookie_recovery(store, tmp_path, monkeypatch):
    source = tmp_path / "import.json"
    source.write_text(json.dumps(export(envelope(b"[]"))), encoding="utf-8")
    before = store.path.read_bytes()
    failure = StorageSafetyError("unsafe storage")

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(store.filesystem, "read_text", fail)
    with pytest.raises(StorageSafetyError) as caught:
        store.import_file(source, PASSPHRASE)
    assert caught.value is failure
    assert store.path.read_bytes() == before


def test_deep_valid_json_attributes_do_not_add_a_validation_recursion_limit(store):
    nested = {"leaf": "value"}
    for _ in range(600):
        nested = {"child": nested}
    payload = json.dumps([dict(RECORD, rest={"nested": nested})]).encode()
    store.path.write_text(json.dumps(envelope(payload)), encoding="utf-8")
    cookie = next(iter(store.load()))
    item = cookie._rest["nested"]
    for _ in range(600):
        item = item["child"]
    assert item == {"leaf": "value"}


def test_internal_attribute_validation_rejects_cycles_and_non_json_values():
    cyclic = []
    cyclic.append(cyclic)
    assert not cookies._is_json_value(cyclic)
    assert not cookies._is_json_value({1: "value"})
    assert not cookies._is_json_value({"value": object()})
    shared = {"value": "safe"}
    assert cookies._is_json_value([shared, shared])
