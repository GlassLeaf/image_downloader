from __future__ import annotations

import asyncio
import gzip
import io
import json
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
import yaml

from image_downloader.devtools.catalog import CATALOG, select_features
from image_downloader.devtools.cli import main
from image_downloader.devtools.generation import PNG, apply_changes, create_project, extend_project
from image_downloader.devtools.inputs import decode_input
from image_downloader.devtools.loading import DevelopmentLoader, resolve_target
from image_downloader.devtools.output import RawOutput
from image_downloader.devtools.runtime import DevelopmentSession
from image_downloader.devtools.transport import FixtureMismatchError, FixtureTransport, TraceTransport
from image_downloader.models import AdditionalFileSpec, ImageResource
from image_downloader.plugins.plugin_manifest import read_manifest
from image_downloader.plugins.plugin_verification import verify_signed_plugin_source


def project(tmp_path: Path, features=None, config=None, **extra) -> Path:
    directory = tmp_path / "project"
    create_project(directory, {"features": features or ["json", "cleanup"], "config": config or {}, **extra})
    return directory


@asynccontextmanager
async def opened(directory: Path, tmp_path: Path, **options):
    output = RawOutput(tmp_path / "run", stream=io.StringIO())
    session = DevelopmentSession({"target": str(directory), **options}, output)
    try:
        await session.open()
        yield session
    finally:
        await session.close()


def fixture(directory: Path, requests: list) -> Path:
    path = directory / "fixture.yaml"
    path.write_text(yaml.safe_dump({"requests": requests}), encoding="utf-8")
    return path


def image_entry(url="https://example.test/image.png", **extra):
    import base64

    return {
        "match": {"url": url},
        "response": {"headers": {"content-type": "image/png"}, "body_base64": base64.b64encode(PNG).decode(), **extra},
    }


def gallery_entry(**extra):
    return {
        "match": {"url": "https://example.test/gallery"},
        "response": {"json": {"title": "Book", "images": [{"id": "image1", "url": "/image.png"}], **extra}},
    }


@pytest.mark.parametrize("feature", list(CATALOG))
def test_every_feature_generates_loadable_typed_v3_contract(tmp_path: Path, feature: str):
    directory = project(tmp_path, [feature])
    record = resolve_target(directory)
    output = RawOutput(tmp_path / "events", stream=io.StringIO())
    loader = DevelopmentLoader(output)
    try:
        instance = loader.typed_instance(record)
        instance.validate_config(record.author_defaults, {})
        for hook in CATALOG[feature].hooks:
            assert callable(getattr(instance, hook))
        if feature != "updates":
            assert not hasattr(instance, "check_updates")
    finally:
        loader.close()


@pytest.mark.parametrize("feature", [name for name, item in CATALOG.items() if "image_processor_plugin" in item.kinds])
def test_processor_generation(tmp_path: Path, feature: str):
    directory = project(tmp_path, [feature], kind="image_processor_plugin")
    record = resolve_target(directory)
    loader = DevelopmentLoader(RawOutput(tmp_path / "out", stream=io.StringIO()))
    try:
        instance = loader.typed_instance(record)
        assert callable(instance.transform)
        assert not hasattr(instance, "inspect")
    finally:
        loader.close()


def test_incompatible_features_and_incomplete_implementation(tmp_path):
    with pytest.raises(ValueError, match="authentication"):
        select_features(["oauth", "csrf"], "site_plugin")
    with pytest.raises(ValueError, match="either"):
        select_features(["html", "json"], "site_plugin")
    with pytest.raises(ValueError, match="unavailable"):
        select_features(["html"], "image_processor_plugin")
    directory = project(tmp_path, ["json", "oauth"])

    async def scenario():
        async with opened(directory, tmp_path) as session:
            report = await session.check()
            assert not report["valid"]
            assert "token_url" in report["unimplemented"][0]["message"]

    asyncio.run(scenario())


def test_preview_uses_fixture_in_detached_gateway_and_preserves_raw_secrets(tmp_path):
    directory = project(tmp_path, ["json", "api-key", "cleanup"], secrets={"api_token": "RAW-API-SECRET"})
    path = fixture(tmp_path, [gallery_entry()])

    async def scenario():
        async with opened(directory, tmp_path, fixture=str(path), record=True) as session:
            result = await session.simulate("https://example.test/gallery", "preview")
            assert not result.failures
            assert result.image_requests[0].effective_request is not None
            headers = result.image_requests[0].effective_request.headers
            assert any(item.value == "RAW-API-SECRET" for item in headers)
            assert not list((session.output.directory / "data").rglob("*.png"))
            events = (session.output.directory / "events.jsonl").read_text(encoding="utf-8")
            assert "RAW-API-SECRET" in events
            assert "<redacted>" not in events
            assert len(session.output.recorded_http) == 1

    asyncio.run(scenario())


def test_replay_download_transforms_and_all_additional_hook_points(tmp_path):
    directory = project(
        tmp_path,
        ["json", "additional-files", "transform", "history", "metadata", "cleanup"],
        {"additional_points": ["all"]},
    )
    path = fixture(tmp_path, [gallery_entry(), image_entry()])

    async def scenario():
        async with opened(directory, tmp_path, fixture=str(path)) as session:
            result = await session.simulate("https://example.test/gallery", "replay")
            assert not result.failures
            assert len(result.saved_files) == 1
            assert Path(result.saved_files[0]).is_file()
            assert {item.point.value for item in result.additional_files} == {
                "before_manifest",
                "after_manifest",
                "before_image_request",
                "after_image_request",
                "before_image_save",
                "after_image_save",
                "after_download",
            }
            assert not [item for item in result.additional_files if item.status == "failed"]
            events = (session.output.directory / "events.jsonl").read_text(encoding="utf-8")
            assert "generated-transform" in events
            assert ".additional_file_received" in events
            assert ".additional_file_saved" in events
            assert ".cleanup_after_use" in events

    asyncio.run(scenario())


def test_auth_refresh_uses_core_retry_and_origin_rules(tmp_path):
    directory = project(
        tmp_path,
        ["json", "oauth", "cleanup"],
        {"token_url": "https://example.test/token"},
        secrets={"access_token": "OLD-TOKEN", "refresh_token": "REFRESH-SECRET"},
    )
    path = fixture(
        tmp_path,
        [
            {
                "match": {"url": "https://example.test/gallery"},
                "responses": [{"status": 401}, gallery_entry()["response"]],
            },
            {
                "match": {"url": "https://example.test/token", "method": "POST"},
                "response": {"json": {"access_token": "NEW-TOKEN"}},
            },
            image_entry(),
        ],
    )

    async def scenario():
        async with opened(directory, tmp_path, fixture=str(path)) as session:
            result = await session.simulate("https://example.test/gallery", "replay")
            assert not result.failures
            events = (session.output.directory / "events.jsonl").read_text(encoding="utf-8")
            assert all(token in events for token in ("OLD-TOKEN", "NEW-TOKEN", "REFRESH-SECRET", "auth.refresh"))

    asyncio.run(scenario())


def test_short_lived_url_is_reissued_by_core(tmp_path):
    directory = project(
        tmp_path, ["json", "short-lived", "cleanup"], {"resolve_url": "https://example.test/resolve/{id}"}
    )
    path = fixture(
        tmp_path,
        [
            gallery_entry(),
            {
                "match": {"url": "https://example.test/resolve/image1"},
                "responses": [{"json": {"url": "/expired.png"}}, {"json": {"url": "/image.png"}}],
            },
            {"match": {"url": "https://example.test/expired.png"}, "response": {"status": 403}},
            image_entry(),
        ],
    )

    async def scenario():
        async with opened(directory, tmp_path, fixture=str(path)) as session:
            result = await session.simulate("https://example.test/gallery", "replay")
            assert not result.failures
            events = (session.output.directory / "events.jsonl").read_text(encoding="utf-8")
            assert ".recover_image_request" in events

    asyncio.run(scenario())


def test_pagination_cycle_is_not_reported_as_partial_success(tmp_path):
    directory = project(tmp_path, ["json", "pagination", "cleanup"])
    path = fixture(
        tmp_path,
        [
            gallery_entry(next_cursor="same"),
            {
                "match": {"url": "https://example.test/gallery?cursor=same"},
                "response": {"json": {"title": "Book", "images": [], "next_cursor": "same"}},
            },
        ],
    )

    async def scenario():
        async with opened(directory, tmp_path, fixture=str(path)) as session:
            with pytest.raises(Exception, match="plugin hook failed") as failure:
                await session.simulate("https://example.test/gallery", "preview")
            assert "pagination cycle" in str(failure.value.__cause__)

    asyncio.run(scenario())


def test_arbitrary_calls_return_references_and_sync_contract_validation(tmp_path):
    directory = project(tmp_path)
    source = directory / "plugin/plugin.py"
    source.write_text(
        source.read_text(encoding="utf-8")
        + """
    def increment(self, value=1):
        self.counter = getattr(self, "counter", 0) + value
        return self.counter

    async def _echo(self, value):
        return value
""",
        encoding="utf-8",
    )

    async def scenario():
        async with opened(directory, tmp_path) as session:
            assert await session.call("increment", {"args": [2]}, save_as="first") == 2
            assert await session.call("increment", {"args": [{"$ref": "first"}]}) == 4
            assert await session.call("_echo", {"args": [{"$ref": "last"}]}) == 4
            assert await session.call("matches", {"args": ["https://example.test/gallery"]}) is True
            assert not session.context
            session.instance.matches = lambda url: "RAW-TOKEN"
            with pytest.raises(Exception, match="must return bool"):
                await session.call("matches", {"args": ["https://example.test/gallery"]})
            assert "RAW-TOKEN" in (session.output.directory / "events.jsonl").read_text(encoding="utf-8")

    asyncio.run(scenario())


def test_explicit_dtos_binary_and_nested_references(tmp_path):
    path = tmp_path / "data.bin"
    path.write_bytes(b"\x00raw\xff")
    result = decode_input(
        {"$type": "AdditionalFileSpec", "file_id": "x", "relative_path": "x.bin", "data": {"$file": "data.bin"}},
        root=tmp_path,
        refs={},
    )
    assert isinstance(result, AdditionalFileSpec)
    assert result.data == path.read_bytes()
    output = RawOutput(tmp_path / "out", binary="hex", stream=io.StringIO())
    rendered = output.value(result)
    assert rendered["data"]["hex"] == "00726177ff"
    assert output.value(ImageResource("https://example.test/image?token=RAW"))["url"].endswith("token=RAW")
    with pytest.raises(ValueError, match="unknown public"):
        decode_input({"$type": "os.system"}, root=tmp_path, refs={})


def test_fixture_sequences_matching_compression_recording_and_no_fallback(tmp_path):
    import base64

    path = fixture(
        tmp_path,
        [
            {
                "match": {"url": "https://example.test/a", "json": {"x": 1}, "method": "POST"},
                "responses": [
                    {"status": 401},
                    {
                        "headers": {"content-encoding": "gzip"},
                        "body_base64": base64.b64encode(gzip.compress(b"raw-secret")).decode(),
                    },
                ],
            }
        ],
    )

    async def scenario():
        output = RawOutput(tmp_path / "out", stream=io.StringIO())
        replay = FixtureTransport(path)
        async with httpx.AsyncClient(transport=TraceTransport(replay, output, record=True)) as client:
            assert (await client.post("https://example.test/a", json={"x": 1})).status_code == 401
            assert (await client.post("https://example.test/a", json={"x": 1})).content == b"raw-secret"
            with pytest.raises(FixtureMismatchError):
                await client.get("https://unregistered.test/")
        recording = FixtureTransport(output.directory / "recording.yaml")
        async with httpx.AsyncClient(transport=recording) as client:
            assert (await client.post("https://example.test/a", json={"x": 1})).status_code == 401
            assert (await client.post("https://example.test/a", json={"x": 1})).content == b"raw-secret"

    asyncio.run(scenario())


def test_extension_preserves_manual_code_and_is_idempotent(tmp_path):
    directory = project(tmp_path)
    source = directory / "plugin/plugin.py"
    source.write_text(
        source.read_text(encoding="utf-8") + '\n    def manual(self):\n        return "keep"\n', encoding="utf-8"
    )
    before = source.read_bytes()
    preview = extend_project(directory, {"features": ["updates", "additional-files"]})
    assert "check_updates" in preview["diff"]
    assert source.read_bytes() == before
    result = extend_project(directory, {"features": ["updates", "additional-files"]}, apply=True)
    assert result["applied"] and not result["conflicts"]
    assert 'return "keep"' in source.read_text(encoding="utf-8")
    assert (Path(result["backup"]) / "files.json").is_file()
    repeated = extend_project(directory, {"features": ["updates", "additional-files"]})
    assert not repeated["conflicts"]
    assert not repeated["diff"]


def test_extension_conflicts_and_preapply_change_detection(tmp_path):
    directory = project(tmp_path)
    source = directory / "plugin/plugin.py"
    source.write_text(
        source.read_text(encoding="utf-8").replace(
            "def auth_flow(self, context: PluginExecutionContext) -> AuthFlow | None:\n        return None",
            'def auth_flow(self, context: PluginExecutionContext) -> AuthFlow | None:\n        return "manual"',
        ),
        encoding="utf-8",
    )
    before = source.read_bytes()
    result = extend_project(directory, {"features": ["api-key"]}, apply=True)
    assert "plugin.py:auth_flow" in result["conflicts"]
    assert not result["applied"] and source.read_bytes() == before
    with pytest.raises(RuntimeError, match="changed before apply"):
        apply_changes(directory, {source: b"new"}, {source: b"outdated"})


def test_cli_child_json_channels_raw_output_and_signing(tmp_path, capfd):
    directory = project(tmp_path)
    source = directory / "plugin/plugin.py"
    source.write_text(
        source.read_text(encoding="utf-8")
        + """
    def debug(self):
        import logging
        import os
        import subprocess
        import sys
        print("STDOUT password=VISIBLE")
        print("STDERR token=VISIBLE", file=sys.stderr)
        os.write(1, b"NATIVE-STDOUT VISIBLE\\n")
        os.write(2, b"NATIVE-STDERR VISIBLE\\n")
        sys.stdout.buffer.write(b"BINARY-STDOUT VISIBLE\\n")
        subprocess.run([sys.executable, "-c", "print('CHILD-STDOUT VISIBLE')"], check=True)
        logging.getLogger(__name__).warning("authorization=VISIBLE")
        return {"secret": "VISIBLE"}
""",
        encoding="utf-8",
    )
    assert main(["call", str(directory), "debug", "--json"]) == 0
    captured = capfd.readouterr()
    reply = json.loads(captured.out)
    assert reply["result"] == {"secret": "VISIBLE"}
    assert "STDOUT password=VISIBLE" in captured.err and "STDERR token=VISIBLE" in captured.err
    assert "NATIVE-STDOUT VISIBLE" in captured.err and "NATIVE-STDERR VISIBLE" in captured.err
    assert "CHILD-STDOUT VISIBLE" in captured.err and "BINARY-STDOUT VISIBLE" in captured.err
    events = (Path(reply["run_directory"]) / "events.jsonl").read_text(encoding="utf-8")
    assert "authorization=VISIBLE" in events
    assert "<redacted>" not in events
    assert main(["sign", str(directory), "--key", str(tmp_path / "signing.pem"), "--create-key", "--json"]) == 0
    capfd.readouterr()
    verify_signed_plugin_source(read_manifest(directory / "plugin"))


def test_cli_load_failure_and_cleanup_failure_return_one(tmp_path, capfd):
    directory = project(tmp_path)
    source = directory / "plugin/plugin.py"
    source.write_text(
        source.read_text(encoding="utf-8").replace(
            "return await self._dev_support().cleanup_after_use()", 'raise ValueError("RAW-CLEANUP-SECRET")'
        ),
        encoding="utf-8",
    )
    assert main(["load", str(directory), "--json"]) == 1
    captured = capfd.readouterr()
    assert "RAW-CLEANUP-SECRET" in captured.out
    source.write_text("this is invalid Python !!!", encoding="utf-8")
    assert main(["load", str(source), "--json"]) == 1
    captured = capfd.readouterr()
    assert "SyntaxError" in captured.out


def test_cli_invalid_timeout_does_not_start_worker(tmp_path, capfd):
    assert main(["load", str(tmp_path), "--timeout", "nan", "--json"]) == 2
    assert "finite positive" in capfd.readouterr().out


@pytest.mark.parametrize(
    "features,config",
    [
        (["html"], {"image_selector": "div.gallery img.image", "image_attribute": "data-src"}),
        (["html", "pagination"], {}),
        (["html", "chapters"], {"chapter_selector": "a.chapter"}),
        (["html", "csrf"], {"login_url": "https://example.test/login"}),
        (["json", "api-key"], {}),
        (["json", "bearer"], {}),
        (["json", "oauth"], {"token_url": "https://example.test/token"}),
        (["json", "short-lived"], {"resolve_url": "https://example.test/resolve/{id}"}),
        (["json", "pagination", "chapters"], {}),
        (
            [
                "json",
                "pagination",
                "chapters",
                "oauth",
                "short-lived",
                "transform",
                "history",
                "metadata",
                "additional-files",
                "output-values",
                "save-options",
                "empty-policy",
                "cleanup",
            ],
            {
                "token_url": "https://example.test/token",
                "resolve_url": "https://example.test/resolve/{id}",
                "additional_points": ["all"],
            },
        ),
    ],
)
def test_generated_feature_fixtures_execute_without_hand_written_responses(tmp_path, features, config):
    if "html" in features:
        pytest.importorskip("selectolax")
    directory = project(tmp_path, features, config)

    async def scenario():
        async with opened(directory, tmp_path, fixture=str(directory / "fixtures/replay.yaml")) as session:
            result = await session.simulate("https://example.test/gallery", "replay")
            assert not result.failures
            assert result.saved_files
            assert not [item for item in result.additional_files if item.status == "failed"]

    asyncio.run(scenario())


def test_extending_existing_unmanaged_plugin_and_relative_imports(tmp_path):
    from support.plugins import make_minimal_plugin

    directory = make_minimal_plugin(tmp_path / "existing")
    source = directory / "entry.source"
    source.write_text(
        source.read_text(encoding="utf-8") + '\n    def manual(self):\n        return "keep"\n', encoding="utf-8"
    )
    result = extend_project(directory, {"features": ["updates"]}, apply=True)
    assert not result["conflicts"]
    assert result["applied"]
    record = resolve_target(directory / "manifest.json")
    loader = DevelopmentLoader(RawOutput(tmp_path / "out", stream=io.StringIO()))
    try:
        instance = loader.typed_instance(record)
        assert instance.manual() == "keep"
        assert callable(instance.check_updates)
    finally:
        loader.close()


def test_existing_profile_credentials_and_cookies_are_read_only(tmp_path, monkeypatch):
    from support.vault import MemoryKeys

    from image_downloader.credentials.keyring import MasterKeyStore
    from image_downloader.credentials.vault import CredentialVault
    from image_downloader.storage.cookies import CookieStore
    from image_downloader.storage.filesystem import FileSystem

    keys = MemoryKeys()
    monkeypatch.setattr(MasterKeyStore, "get", lambda self, vault_id, key_id: keys.get(vault_id, key_id))
    monkeypatch.setattr(
        MasterKeyStore, "create", lambda self, vault_id, key_id, key: keys.create(vault_id, key_id, key)
    )
    source_root = tmp_path / "normal-data"
    store = CredentialVault(source_root).profile("normal")
    store.set_plugin("com.example.gallery", "API_TOKEN", "PROFILE-SECRET")
    jar = httpx.Cookies()
    jar.set("session", "PROFILE-COOKIE", domain="example.test")
    cookie = CookieStore(FileSystem(source_root / "profiles/normal/cookie"), store=store)
    cookie.save(jar.jar)
    before = {
        path.relative_to(source_root): path.read_bytes()
        for path in source_root.rglob("*")
        if path.is_file() and path.suffix != ".lock"
    }
    directory = project(tmp_path, ["json", "api-key", "cleanup"])
    path = fixture(tmp_path, [gallery_entry()])

    async def scenario():
        async with opened(
            directory, tmp_path, profile="normal", profile_data_root=str(source_root), fixture=str(path)
        ) as session:
            result = await session.simulate("https://example.test/gallery", "preview")
            assert not result.failures
            events = (session.output.directory / "events.jsonl").read_text(encoding="utf-8")
            assert "PROFILE-SECRET" in events and "PROFILE-COOKIE" in events
            session.gateway.client.cookies.set("session", "CHANGED", domain="example.test")

    asyncio.run(scenario())
    assert {
        path.relative_to(source_root): path.read_bytes()
        for path in source_root.rglob("*")
        if path.is_file() and path.suffix != ".lock"
    } == before


def test_cli_persistent_session_and_reload(tmp_path, capfd, monkeypatch):
    directory = project(tmp_path)
    source = directory / "plugin/plugin.py"
    source.write_text(
        source.read_text(encoding="utf-8")
        + """
    def increment(self, value=1):
        self.counter = getattr(self, "counter", 0) + value
        return self.counter
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(
            'call increment {"args":[2],"save_as":"first"}\n'
            'call increment {"args":[{"$ref":"first"}]}\n'
            "reload\ncall increment {}\nquit\n"
        ),
    )
    assert main(["session", str(directory), "--json"]) == 0
    replies = [json.loads(line) for line in capfd.readouterr().out.splitlines()]
    assert [item["result"] for item in replies[:4]] == [2, 4, {"reloaded": True}, 1]


def test_cli_timeout_cleans_up_worker(tmp_path, capfd):
    import time

    directory = project(tmp_path)
    source = directory / "plugin/plugin.py"
    source.write_text(
        source.read_text(encoding="utf-8")
        + """
    async def wait_forever(self):
        import asyncio
        await asyncio.sleep(3600)

    async def cleanup_after_use(self):
        from pathlib import Path
        Path(__file__).with_suffix(".cleanup").write_text("closed", encoding="utf-8")
""",
        encoding="utf-8",
    )
    start = time.monotonic()
    assert main(["call", str(directory), "wait_forever", "--timeout", "10", "--json"]) == 1
    assert time.monotonic() - start < 25
    assert "timed out" in capfd.readouterr().out
    assert source.with_suffix(".cleanup").read_text(encoding="utf-8") == "closed"


def test_cli_bad_method_input_has_input_exit_code(tmp_path, capfd):
    directory = project(tmp_path)
    assert main(["call", str(directory), "create_image_request", "--json"]) == 2
    assert "image" in capfd.readouterr().out


def test_manual_generated_method_remains_manual_after_subsequent_extensions(tmp_path):
    directory = project(tmp_path)
    source = directory / "plugin/plugin.py"
    source.write_text(
        source.read_text(encoding="utf-8").replace(
            "return self._dev_support().matches(url, DEFAULT_CONFIG)", "return True  # manual matching"
        ),
        encoding="utf-8",
    )
    result = extend_project(directory, {"features": ["updates"]}, apply=True)
    assert result["applied"], result["conflicts"]
    assert extend_project(directory, {"features": ["output-values"]}, apply=True)["applied"]
    assert "return True  # manual matching" in source.read_text(encoding="utf-8")


def test_extension_updates_owned_fixture_and_preserves_edited_fixture(tmp_path):
    directory = project(tmp_path)
    assert extend_project(directory, {"features": ["api-key"]}, apply=True)["applied"]
    path = directory / "fixtures/replay.yaml"
    assert "DEVELOPMENT-API-TOKEN" in path.read_text(encoding="utf-8")

    async def scenario():
        async with opened(directory, tmp_path, fixture=str(path)) as session:
            result = await session.simulate("https://example.test/gallery", "replay")
            assert not result.failures and result.saved_files

    asyncio.run(scenario())
    path.write_text(path.read_text(encoding="utf-8") + "\n# manual fixture\n", encoding="utf-8")
    result = extend_project(directory, {"features": ["pagination"]}, apply=True)
    assert not result["applied"]
    assert "fixtures/replay.yaml" in result["conflicts"]
    assert "fixtures/replay.yaml" in result["suggestions"]


def test_simulation_combines_site_and_generated_processor(tmp_path):
    directory = project(tmp_path)
    processor = tmp_path / "processor"
    create_project(processor, {"kind": "image_processor_plugin", "features": ["transform", "history", "cleanup"]})

    async def scenario():
        async with opened(
            directory, tmp_path, fixture=str(directory / "fixtures/replay.yaml"), processors=[str(processor)]
        ) as session:
            result = await session.simulate("https://example.test/gallery", "replay")
            assert result.saved_files and not result.failures
            events = (session.output.directory / "events.jsonl").read_text(encoding="utf-8")
            assert "com.example.processor.transform" in events

    asyncio.run(scenario())


def test_additional_file_failure_returns_failure_exit_code(tmp_path, capfd):
    directory = project(
        tmp_path, ["json", "additional-files", "cleanup"], {"additional_url": "https://example.test/unregistered"}
    )
    path = fixture(tmp_path, [gallery_entry(), image_entry()])
    assert (
        main(
            [
                "simulate",
                str(directory),
                "https://example.test/gallery",
                "--mode",
                "replay",
                "--fixture",
                str(path),
                "--json",
            ]
        )
        == 1
    )
    reply = json.loads(capfd.readouterr().out)
    assert any(item["status"] == "failed" for item in reply["result"]["additional_files"])
