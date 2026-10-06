"""Offline probes asserting current behavior, not the desired repaired behavior.

Only temporary files, fake secrets, mock HTTP, and small images are used.
"""

from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import struct
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests/v3/plugins"))
sys.path.insert(0, str(ROOT / "tests/v3/application"))

from test_generic_workflow import compose  # noqa: E402
from test_plugin_v3 import make_plugin  # noqa: E402
from test_workflow_verification import make_run  # noqa: E402

from image_downloader import AppConfig, ImageSaveOptions, WorkflowStateService  # noqa: E402
from image_downloader.application.workflow_verification import verify_workflow  # noqa: E402
from image_downloader.configuration.layers import _sanitize_user_layer, validate_config  # noqa: E402
from image_downloader.exceptions import AuthenticationError, ConfigurationError  # noqa: E402
from image_downloader.media.image_processor import ImageProcessor, _inspect_image, _process_image  # noqa: E402
from image_downloader.observability.notifications import _send_mail  # noqa: E402
from image_downloader.plugins.lifecycle import (  # noqa: E402
    PluginClassLoader,
    PluginDiscovery,
    PluginManifestVerifier,
)
from image_downloader.plugins.management import trust_plugin  # noqa: E402
from image_downloader.plugins.plugin_manifest import read_manifest, verify_signed_plugin_source  # noqa: E402
from image_downloader.plugins.runtime import PluginRuntime  # noqa: E402
from image_downloader.storage.cookies import CookieStore, _deserialize  # noqa: E402
from image_downloader.storage.filesystem import FileSystem  # noqa: E402
from image_downloader.transport.gateway import RequestGateway  # noqa: E402


def observation(probe: str, **fields: object) -> None:
    print(json.dumps({"probe": probe, **fields}, ensure_ascii=False))


def png(color: str = "red") -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (2, 2), color).save(stream, "PNG")
    return stream.getvalue()


def install_mock_http(monkeypatch: pytest.MonkeyPatch, respond) -> None:
    client_class = httpx.AsyncClient

    def client(*args, **kwargs):
        return client_class(*args, **dict(kwargs, transport=httpx.MockTransport(respond)))

    monkeypatch.setattr(httpx, "AsyncClient", client)


def test_qs001_unsigned_bytecode_passes_strict_verification(tmp_path: Path) -> None:
    import marshal

    root = tmp_path / "plugins"
    directory = make_plugin(root)
    trust_plugin(root, directory)
    manifest = read_manifest(directory)
    source = directory / manifest.value["entry"]["file"]
    original = source.read_bytes()
    code = compile(original.decode() + "\nEntry.audit_unsigned_payload = True\n", str(source), "exec")
    cache = Path(importlib.util.cache_from_source(str(source)))
    cache.parent.mkdir()
    # A timestamp cache matching the signed source; its body only sets a class marker.
    header = importlib.util.MAGIC_NUMBER + struct.pack(
        "<III",
        0,
        int(source.stat().st_mtime) & 0xFFFFFFFF,
        len(original),
    )
    cache.write_bytes(header + marshal.dumps(code))
    verify_signed_plugin_source(read_manifest(directory))
    runtime = PluginRuntime(AppConfig(), root, mode="strict")
    try:
        runtime.prepare()
        loaded = runtime.site_instance(runtime.records[manifest.id])
        assert loaded.audit_unsigned_payload is True
        assert source.read_bytes() == original
        verify_signed_plugin_source(read_manifest(directory))
        observation(
            "QS-001-cache", strict_verification_passed=True, unsigned_marker_executed=True, signed_source_unchanged=True
        )
    finally:
        runtime.close()


def test_qs001_source_changes_between_verification_and_load(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    directory = make_plugin(root)
    trust_plugin(root, directory)
    verified = PluginManifestVerifier(root, mode="strict").verify(PluginDiscovery(root).discover())
    assert len(verified.records) == 1
    record = verified.records[0]
    source = directory / record.manifest.value["entry"]["file"]
    source.write_text(source.read_text() + "\nEntry.audit_changed_after_verification = True\n")
    loader = PluginClassLoader()
    try:
        assert loader.load_class(record).audit_changed_after_verification is True
        observation("QS-001-recheck", changed_source_executed=True, scheduling="deterministic boundary substitution")
    finally:
        loader.close()


def test_qs002_referrer_contains_cross_origin_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.host == "gallery.test":
            return httpx.Response(200, text='<title>page</title><img src="http://asset.test/a.png">')
        captured.append(request.headers.get("referer", ""))
        return httpx.Response(200, content=png(), headers={"content-type": "image/png"})

    install_mock_http(monkeypatch, respond)

    async def scenario():
        async with compose(tmp_path) as service:
            result = await service.run(
                "https://audit-user:audit-pass@gallery.test/?token=audit-token#private=audit-fragment"
            )
            assert len(result.saved_files) == 1

    asyncio.run(scenario())
    assert len(captured) == 1
    assert "audit-token" in captured[0]
    assert "audit-pass" in captured[0]
    observation("QS-002", cross_origin_http_referrer=captured[0])


def test_qs003_frame_budget_and_worker_deadline_are_absent() -> None:
    stream = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(
        stream,
        "GIF",
        save_all=True,
        append_images=[Image.new("RGB", (2, 2), color) for color in ("blue", "green")],
        duration=100,
        loop=0,
    )
    assert _inspect_image(stream.getvalue(), 4) == "image/gif"
    timeouts = []

    class FakeFuture:
        def result(self, timeout=None):
            timeouts.append(timeout)
            return "image/png"

    processor = ImageProcessor.__new__(ImageProcessor)
    processor._executor = SimpleNamespace(submit=lambda *args: FakeFuture())
    processor._lifecycle_lock = threading.Lock()
    processor._closed = False
    assert processor.inspect(b"small-mock") == "image/png"
    assert timeouts == [None]
    assert AppConfig().media.max_image_pixels is None
    observation(
        "QS-003",
        default_pixel_limit=None,
        per_frame_limit=4,
        accepted_total_frame_pixels=12,
        future_result_timeout=timeouts[0],
        resource_exhaustion_actually_executed=False,
    )


def test_qs004_separate_urls_overwrite_same_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first_bytes, second_bytes = png("red"), png("blue")

    def respond(request):
        if request.url.path in {"/one", "/two"}:
            name = "red" if request.url.path == "/one" else "blue"
            return httpx.Response(200, text=f'<title>Same</title><img src="/{name}.png">')
        return httpx.Response(
            200,
            content=first_bytes if request.url.path == "/red.png" else second_bytes,
            headers={"content-type": "image/png"},
        )

    install_mock_http(monkeypatch, respond)

    async def scenario():
        async with compose(tmp_path) as service:
            first = await service.run("https://example.test/one")
            assert Path(first.saved_files[0]).read_bytes() == first_bytes
            second = await service.run("https://example.test/two")
            assert first.saved_files == second.saved_files
            assert Path(first.saved_files[0]).read_bytes() == second_bytes
            observation("QS-004", identical_path=True, previous_bytes_overwritten=True)

    asyncio.run(scenario())


def test_qs005_empty_download_is_completed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install_mock_http(
        monkeypatch, lambda request: httpx.Response(200, text="<title>Sign in</title><p>Login required</p>")
    )

    async def scenario():
        async with compose(tmp_path) as service:
            first = await service.workflow("https://example.test/", workflow_retries=0)
            assert first.items[0].status == "success"
            assert not first.items[0].download.saved_files
            state = WorkflowStateService(service.config).get_workflow("https://example.test/")[0]
            assert all(item.completed for item in state.items)
            repeat = await service.workflow("https://example.test/", workflow_retries=0)
            assert not repeat.selected_urls
            observation("QS-005", empty_status="success", completed=True, repeat_selected=0)

    asyncio.run(scenario())


def test_qs006_unknown_settings_are_discarded() -> None:
    sanitized, removals = _sanitize_user_layer({"network": {"max_retries": 9, "requset_timeout_seconds": 90}})
    assert sanitized == {"network": {}}
    assert len(removals) == 2
    observation("QS-006", removed=[".".join(path) for path in removals], sanitized=sanitized)


def test_qs006_cleanup_rewrites_the_user_file(tmp_path: Path) -> None:
    from image_downloader.configuration.layers import _apply_cleanup, _LayerCleanup

    source = b"network:\n  max_retries: 9\n  requset_timeout_seconds: 90\n  max_attempts: 2\n"
    path = tmp_path / "app.yaml"
    path.write_bytes(source)
    _, removals = _sanitize_user_layer(
        {"network": {"max_retries": 9, "requset_timeout_seconds": 90, "max_attempts": 2}}
    )
    _apply_cleanup(_LayerCleanup(path, source, removals))
    remaining = path.read_text()
    assert "max_retries" not in remaining and "requset_timeout_seconds" not in remaining
    assert "max_attempts: 2" in remaining
    observation("QS-006-rewrite", unknown_keys_removed_from_file=True, valid_setting_preserved=True)


def test_qs007_verify_accepts_nonimage(tmp_path: Path) -> None:
    result = verify_workflow(make_run(tmp_path), tmp_path)
    assert result["status"] == "passed"
    observation("QS-007", nonimage_verification=result["status"])


def test_qs008_missing_artifact_does_not_trigger_updated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def respond(request):
        if request.url.path == "/":
            return httpx.Response(200, text='<title>Same</title><img src="/a.png">')
        return httpx.Response(200, content=png(), headers={"content-type": "image/png"})

    install_mock_http(monkeypatch, respond)

    async def scenario():
        async with compose(tmp_path) as service:
            first = await service.workflow("https://example.test/", workflow_retries=0)
            Path(first.items[0].download.saved_files[0]).unlink()
            repeat = await service.workflow("https://example.test/", workflow_retries=0)
            assert not repeat.selected_urls
            observation("QS-008", file_missing=True, repeat_selected=0)

    asyncio.run(scenario())


def test_qs009_animation_loses_frames_on_conversion() -> None:
    stream = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(
        stream, "GIF", save_all=True, append_images=[Image.new("RGB", (2, 2), "blue")], duration=100, loop=0
    )
    output, _ = _process_image(
        stream.getvalue(), "https://example.test/a.gif", "image/gif", ImageSaveOptions(format="WEBP"), None
    )
    with Image.open(io.BytesIO(output)) as converted:
        assert converted.n_frames == 1
        observation("QS-009", original_frames=2, converted_frames=converted.n_frames)


def test_qs010_large_number_escapes_validation() -> None:
    with pytest.raises(OverflowError):
        validate_config({"network": {"request_timeout_seconds": 10**400}})
    for bad in (float("nan"), float("inf"), True, "30", 0, -1):
        with pytest.raises(ConfigurationError):
            validate_config({"network": {"request_timeout_seconds": bad}})
    observation("QS-010", large_integer_exception="OverflowError", usual_invalid_values="ConfigurationError")


def test_qs011_cookie_import_root_type_escapes_error_mapping(tmp_path: Path) -> None:
    source = tmp_path / "invalid-export.json"
    source.write_text("[]", encoding="utf-8")
    store = CookieStore(FileSystem(tmp_path / "state"))
    with pytest.raises(AttributeError):
        store.import_file(source, "audit-fake-passphrase")
    observation("QS-011", invalid_json_root_exception="AttributeError", credential_store_accessed=False)


def test_qs011_cookie_record_required_field_escapes_error_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from image_downloader.storage.cookies import _encrypt

    state = FileSystem(tmp_path / "state")
    fake_key = b"a" * 32
    state.write_bytes_atomic("cookies.enc", json.dumps(_encrypt(b"[{}]", fake_key)).encode())
    store = CookieStore(state)
    monkeypatch.setattr(store, "_key", lambda **kwargs: fake_key)
    with pytest.raises(KeyError):
        store.load()
    with pytest.raises(ValueError):
        _deserialize("{}")
    observation("QS-011-record", missing_field_exception="KeyError", scope="valid AES-GCM container, fake key")


def test_qs013_failed_composition_keeps_plugin_modules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from image_downloader.application.composer import RuntimeComposer

    root = tmp_path / "plugins"
    directory = make_plugin(root)
    trust_plugin(root, directory)
    composer = RuntimeComposer(AppConfig(), config_root=tmp_path, plugin_root=root)
    previous = set(sys.modules)
    captured = []
    original_registry = composer.compose_registry

    def registry():
        result = original_registry()
        captured.append(result)
        return result

    def fail_dependencies():
        raise AuthenticationError("audit fake credential-store failure")

    monkeypatch.setattr(composer, "compose_registry", registry)
    monkeypatch.setattr(composer, "_build_dependencies", fail_dependencies)
    try:
        with pytest.raises(AuthenticationError):
            composer.compose()
        leaked = [name for name in set(sys.modules) - previous if name.startswith("_image_downloader_plugins.")]
        assert leaked
        observation("QS-013", modules_retained_after_composition_failure=len(leaked))
    finally:
        for result in captured:
            result.close()


def test_qs016_secret_environment_names_collide(monkeypatch: pytest.MonkeyPatch) -> None:
    from image_downloader.credentials.plugin_secrets import RuntimeSecrets

    ids = ("com.example.a-b", "com.example.a.b")
    AppConfig.model_validate({"plugin_settings": {name: {"secrets": {"token": "TOKEN"}} for name in ids}})
    monkeypatch.setenv("IMAGE_DOWNLOADER_PLUGIN_COM_EXAMPLE_A_B_TOKEN", "audit-secret-for-first-id")
    values = [RuntimeSecrets(name, {"token": "TOKEN"}).get("token") for name in ids]
    assert values == ["audit-secret-for-first-id"] * 2
    observation(
        "QS-016",
        distinct_valid_plugin_ids=list(ids),
        secret_environment_name_collides=True,
        credential_store_accessed=False,
    )


def test_qs012_partial_smtp_rejection_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    import image_downloader.observability.notifications as notifications

    sent = []

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def send_message(self, mail):
            sent.append(mail)
            return {"rejected@example.test": (550, b"audit rejection")}

    monkeypatch.setattr(notifications.smtplib, "SMTP", FakeSMTP)
    _send_mail(
        {
            "smtp_host": "audit.invalid",
            "smtp_port": 1025,
            "use_tls": False,
            "from": "audit@example.test",
            "to": ["accepted@example.test", "rejected@example.test"],
        },
        "audit",
        "fake message",
    )
    assert len(sent) == 1
    observation("QS-012", rejected_recipient_count=1, returned_without_error=True, real_email_sent=False)


def test_redirect_and_size_controls(monkeypatch: pytest.MonkeyPatch) -> None:
    from image_downloader.exceptions import ResponseSizeLimitError
    from image_downloader.models import RequestSpec

    captured = []

    def respond(request):
        captured.append(request)
        if request.url.host == "initial.test":
            return httpx.Response(302, headers={"location": "https://other.test/"})
        return httpx.Response(200, content=b"12345")

    install_mock_http(monkeypatch, respond)

    async def scenario():
        gateway = RequestGateway(AppConfig.model_validate({"network": {"max_attempts": 1, "max_response_bytes": 4}}))
        try:
            with pytest.raises(ResponseSizeLimitError):
                await gateway.execute(RequestSpec("https://initial.test/", headers={"Authorization": "Bearer audit"}))
            assert "authorization" not in captured[1].headers
        finally:
            await gateway.close()

    asyncio.run(scenario())
    observation("positive-controls", cross_origin_auth_removed=True, oversized_response_rejected=True)
