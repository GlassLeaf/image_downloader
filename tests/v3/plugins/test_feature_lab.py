from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from PIL import Image
from tools.sign_local_site_plugin import sign

from image_downloader import (
    AdditionalFileHookPoint,
    AppConfig,
    AuthenticationError,
    DownloadRequest,
    InspectRequest,
    RuntimeComposer,
    SecretNotFoundError,
    WorkflowPlanRequest,
    WorkflowRequest,
)
from image_downloader.security import PluginRuntime
from image_downloader.storage.cookies import _cookie

ROOT = Path(__file__).resolve().parents[3]
LAB = ROOT / "examples/plugin-v3-feature-lab"


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


@pytest.fixture
def lab(monkeypatch):
    monkeypatch.syspath_prepend(str(LAB))
    return module("feature_lab_test", LAB / "lab.py")


@pytest.fixture
def artificial_stores(monkeypatch):
    values = {}
    monkeypatch.setitem(
        sys.modules,
        "keyring",
        SimpleNamespace(
            get_password=lambda service, account: values.get((service, account)),
            set_password=lambda service, account, value: values.__setitem__((service, account), value),
            delete_password=lambda service, account: values.pop((service, account), None),
        ),
    )
    return values


def configuration(work):
    return AppConfig.model_validate(yaml.safe_load((work / "app.yaml").read_text(encoding="utf-8")))


def test_cookie_login_issuance_and_expiry_are_local_states(monkeypatch, capsys):
    server = module("cookie_status_site_test", LAB / "server.py")
    clock = [1000.0]
    monkeypatch.setattr(server, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    site = server.LabSite()
    cache_headers = []

    def delivered(handler, status, data, **headers):
        cache_headers.append(headers.get("Cache_Control"))

    monkeypatch.setattr(site, "reply", delivered)
    assert site.cookie_status() == "missing"
    site.handle(SimpleNamespace(path="/cookie-login", headers={}))
    assert site.cookie_status() == "ready"
    assert cache_headers == ["no-store"]
    clock[0] += server.COOKIE_MAX_AGE_SECONDS
    assert site.cookie_status() == "expired"
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("strict", [False, True])
def test_every_package_installs_independently(lab, tmp_path, strict):
    lab.prepare(tmp_path, "http://127.0.0.1:8765", "http://127.0.0.1:8766")
    if strict:
        key = Ed25519PrivateKey.generate()  # ephemeral in-memory TEST key, never distributed
        for slug in lab.SLUGS:
            sign(tmp_path / "sources" / slug, key)
        lab.install(tmp_path, strict=True)
    runtime = PluginRuntime(configuration(tmp_path), tmp_path / "plugins", mode="strict" if strict else "bypass-all")
    try:
        runtime.prepare()
        assert len(runtime.records) == 7
        assert not [item for item in runtime.diagnostics if not item.loaded]
        for slug in lab.SLUGS:
            record, _ = runtime.resolve(f"http://127.0.0.1:8765/samples/{slug}/work", fallback_enabled=False)
            assert record.id == lab.plugin_id(slug)
    finally:
        runtime.close()


@pytest.mark.parametrize(
    "slug",
    [
        "additional-files",
        "credential-gallery",
        "cookie-gallery",
        "origin-auth-gallery",
        "output-gallery",
        "configured-host-gallery",
        "short-lived-gallery",
    ],
)
def test_local_end_to_end(lab, tmp_path, artificial_stores, monkeypatch, slug):
    async def scenario():
        with lab.LabSite() as site:
            lab.prepare(tmp_path, site.origin, site.asset_origin)
            for unit in lab.SECRET_UNITS:
                artificial_stores[f"image-downloader.plugin.{lab.plugin_id(unit)}", "LAB_TOKEN"] = "lab-token"
            config = configuration(tmp_path)
            async with RuntimeComposer(
                config, config_root=tmp_path, plugin_root=tmp_path / "plugins"
            ).compose() as service:
                if slug == "cookie-gallery":
                    # An artificial jar, not browser_cookie3 or a browser profile.
                    service.gateway.client.cookies.jar.set_cookie(
                        _cookie("lab_session", site.cookie_token, domain="127.0.0.1")
                    )
                result = await service.download(DownloadRequest(url=f"{site.origin}/samples/{slug}/work"))
                assert not result.failures
                assert len(result.saved_files) == 4
                if slug == "additional-files":
                    assert {item.point for item in result.additional_files} == set(AdditionalFileHookPoint)
                    second = await service.download(DownloadRequest(url=f"{site.origin}/samples/{slug}/work"))
                    assert len(second.skipped_files) == 4
                    assert any(item.status == "skipped" for item in second.additional_files)
                if slug == "output-gallery":
                    for path in result.saved_files:
                        with Image.open(path) as image:
                            assert image.format in {"JPEG", "PNG"}
                            if image.format == "JPEG":
                                assert image.getexif()[315] == "Feature lab"
                            else:
                                assert not image.getexif()
                if slug == "short-lived-gallery":
                    assert len([event for event in site.events if event[0] == "issue"]) == 8
                    assert site.events == [
                        (event, identifier)
                        for identifier in ("1-1", "1-2", "2-1", "2-2")
                        for event in ("issue", "fetch", "issue", "fetch")
                    ]
                if slug == "origin-auth-gallery":
                    assert all(
                        sent for path, sent in site.authorization_seen if "/samples/origin-auth-gallery/" in path
                    )
            raw = "\n".join(path.read_text(encoding="utf-8") for path in tmp_path.rglob("*.log"))
            assert "lab-token" not in raw and site.cookie_token not in raw

    asyncio.run(scenario())


def test_configured_matcher_and_forced_output(lab, tmp_path, artificial_stores):
    async def scenario():
        with lab.LabSite() as site:
            lab.prepare(tmp_path, site.origin, site.asset_origin)
            config = AppConfig.model_validate(yaml.safe_load((tmp_path / "output.yaml").read_text(encoding="utf-8")))
            async with RuntimeComposer(
                config, config_root=tmp_path, plugin_root=tmp_path / "plugins"
            ).compose() as service:
                record = service.registry.records[lab.plugin_id("configured-host-gallery")]
                plugin = service.registry.site_instance(record)
                url = f"{site.origin}/samples/configured-host-gallery/work"
                assert plugin.matches_with_config(url, {"origin": site.origin}, {})
                assert not plugin.matches_with_config(url, {"origin": "http://127.0.0.1:1"}, {})
                assert not plugin.matches_with_config(url, {"origin": "bad"}, {})
                with pytest.raises(ValueError):
                    plugin.validate_config({"origin": "bad"}, {})
                result = await service.download(
                    DownloadRequest(url=f"{site.origin}/samples/output-gallery/work", force_image_format="PNG")
                )
                assert not result.failures
                for path in result.saved_files:
                    assert "cover-" in Path(path).name and "_lab" in Path(path).name
                    with Image.open(path) as image:
                        assert image.format == "PNG"

    asyncio.run(scenario())


def test_origin_allowlist_and_denied_redirect(lab, tmp_path, artificial_stores):
    async def scenario():
        with lab.LabSite() as site:
            lab.prepare(tmp_path, site.origin, site.asset_origin)
            artificial_stores[f"image-downloader.plugin.{lab.plugin_id('origin-auth-gallery')}", "LAB_TOKEN"] = (
                "lab-token"
            )
            config = configuration(tmp_path)
            async with RuntimeComposer(
                config, config_root=tmp_path, plugin_root=tmp_path / "plugins"
            ).compose() as service:
                with pytest.raises(AuthenticationError, match="outside the configured origins"):
                    await service.inspect(
                        InspectRequest(url=f"{site.origin}/samples/origin-auth-gallery/redirect-denied")
                    )
                assert not any(path == "/sink" for path, _ in site.authorization_seen)
            values = yaml.safe_load((tmp_path / "app.yaml").read_text(encoding="utf-8"))
            values["plugin_settings"][lab.plugin_id("origin-auth-gallery")]["config"]["asset_origin"] = (
                "http://127.0.0.1:1"
            )
            config = AppConfig.model_validate(values)
            async with RuntimeComposer(
                config, config_root=tmp_path, plugin_root=tmp_path / "plugins"
            ).compose() as service:
                with pytest.raises(AuthenticationError):
                    await service.download(DownloadRequest(url=f"{site.origin}/samples/origin-auth-gallery/work"))
                assert not any("/image/" in path for path, _ in site.authorization_seen)

    asyncio.run(scenario())


def test_additional_callback_failure_and_cancellation_cleanup(lab, tmp_path, artificial_stores, monkeypatch):
    async def scenario():
        with lab.LabSite() as site:
            lab.prepare(tmp_path, site.origin, site.asset_origin)
            async with RuntimeComposer(
                configuration(tmp_path), config_root=tmp_path, plugin_root=tmp_path / "plugins"
            ).compose() as service:
                record = service.registry.records[lab.plugin_id("additional-files")]
                cls = service.registry.loader.load_class(record)
                cleaned = []
                original = cls.cleanup_after_use

                def cleanup(self):
                    original(self)
                    cleaned.append(not self.variants and not self.saved and self.layout is None)

                async def fail_saved(self, result, context):
                    raise ValueError("fictional callback error")

                monkeypatch.setattr(cls, "cleanup_after_use", cleanup)
                monkeypatch.setattr(cls, "additional_file_saved", fail_saved)
                result = await service.download(DownloadRequest(url=f"{site.origin}/samples/additional-files/work"))
                assert len(result.saved_files) == 4
                assert any(
                    item.phase == "saved_callback" and item.status == "failed" for item in result.additional_files
                )
                assert cleaned == [True]
                started = asyncio.Event()

                async def block(self, hook, context):
                    started.set()
                    await asyncio.Event().wait()

                monkeypatch.setattr(cls, "additional_files", block)
                task = asyncio.create_task(
                    service.download(DownloadRequest(url=f"{site.origin}/samples/additional-files/work"))
                )
                await asyncio.wait_for(started.wait(), 5)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert cleaned == [True, True]

    asyncio.run(scenario())


def test_credential_helper_only_changes_sample_accounts(lab, artificial_stores, monkeypatch, capsys):
    monkeypatch.setattr(lab.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(lab.getpass, "getpass", lambda *args: "lab-token")
    lab.credentials("set")
    assert set(artificial_stores) == {
        (f"image-downloader.plugin.{lab.plugin_id(slug)}", "LAB_TOKEN") for slug in lab.SECRET_UNITS
    }
    lab.credentials("delete")
    assert artificial_stores == {}
    assert "lab-token" not in capsys.readouterr().out


def test_lab_notifications_use_injected_local_senders(lab, tmp_path, artificial_stores):
    messages = []

    class Sender:
        async def send(self, title, message):
            messages.append(message)
            return True

    async def scenario():
        with lab.LabSite() as site:
            lab.prepare(tmp_path, site.origin, site.asset_origin)
            values = yaml.safe_load((tmp_path / "app.yaml").read_text(encoding="utf-8"))
            values["notification"] = {
                "enabled": True,
                "methods": ["desktop", "email"],
                "notify_on": ["download_success"],
                "email": {"smtp_host": "127.0.0.1", "from": "dummy@example.test", "to": ["dummy@example.test"]},
            }
            config = AppConfig.model_validate(values)
            async with RuntimeComposer(
                config, config_root=tmp_path, plugin_root=tmp_path / "plugins"
            ).compose() as service:
                service.notifications.senders = {"desktop": Sender(), "email": Sender()}
                result = await service.download(DownloadRequest(url=f"{site.origin}/samples/output-gallery/work"))
                assert not result.failures
            assert len(messages) == 2
            assert all(site.cookie_token not in message and "lab-token" not in message for message in messages)

    asyncio.run(scenario())


def test_missing_required_additional_metadata(lab, tmp_path, artificial_stores):
    async def scenario():
        with lab.LabSite() as site:
            site.omit_metadata = True
            lab.prepare(tmp_path, site.origin, site.asset_origin)
            async with RuntimeComposer(
                configuration(tmp_path), config_root=tmp_path, plugin_root=tmp_path / "plugins"
            ).compose() as service:
                with pytest.raises(AuthenticationError, match="per-image metadata"):
                    await service.download(DownloadRequest(url=f"{site.origin}/samples/additional-files/work"))

    asyncio.run(scenario())


def test_secret_lookup_and_environment_override(lab, tmp_path, artificial_stores, monkeypatch):
    async def scenario():
        with lab.LabSite() as site:
            lab.prepare(tmp_path, site.origin, site.asset_origin)
            async with RuntimeComposer(
                configuration(tmp_path), config_root=tmp_path, plugin_root=tmp_path / "plugins"
            ).compose() as service:
                url = f"{site.origin}/samples/credential-gallery/work"
                with pytest.raises(SecretNotFoundError):
                    await service.inspect(InspectRequest(url=url))
                artificial_stores[f"image-downloader.plugin.{lab.plugin_id('credential-gallery')}", "LAB_TOKEN"] = (
                    "wrong"
                )
                monkeypatch.setenv(lab.environment_name("credential-gallery"), "lab-token")
                result = await service.inspect(InspectRequest(url=url))
                assert not result.failures

    asyncio.run(scenario())


def test_workflow_plan_retry_retention_and_history(lab, tmp_path, artificial_stores):
    async def scenario():
        with lab.LabSite() as site:
            site.fail_workflow_once = True
            lab.prepare(tmp_path, site.origin, site.asset_origin)
            async with RuntimeComposer(
                configuration(tmp_path), config_root=tmp_path, plugin_root=tmp_path / "plugins"
            ).compose() as service:
                feed = f"{site.origin}/samples/short-lived-gallery/feed"
                plan = await service.plan_workflow(WorkflowPlanRequest(url=feed))
                assert len(plan.selected_urls) == 1
                assert site.events == []
                result = await service.workflow(WorkflowRequest(url=feed, workflow_retries=1, workflow_retry_delay=0))
                assert result.stop_error is None
                assert all(item.status == "success" for item in result.items)
                assert len(result.rounds) == 2
                assert any(image.retained for image in result.items[0].attempts[-1].images)
            from image_downloader import WorkflowStateService
            from image_downloader.application.workflow_verification import verify_workflow

            runs = WorkflowStateService(configuration(tmp_path)).list_runs()
            assert len(runs) == 1
            report = verify_workflow(runs[0], tmp_path / "data/profiles/default/downloads")
            assert not report["errors"]

    asyncio.run(scenario())
