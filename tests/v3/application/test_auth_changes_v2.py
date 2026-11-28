from __future__ import annotations

import asyncio

import pytest
from support.workflow import FEED, Gallery, attach_transport, compose

from image_downloader import (
    DownloadRequest,
    InspectRequest,
    UpdateCandidate,
    UpdateCheckRequest,
    WorkflowPlanRequest,
    WorkflowRequest,
)


@pytest.mark.parametrize(
    "method, request_type",
    [
        ("download", DownloadRequest),
        ("inspect", InspectRequest),
        ("check_updates", UpdateCheckRequest),
        ("plan_workflow", WorkflowPlanRequest),
        ("workflow", WorkflowRequest),
    ],
)
def test_all_operations_propagate_replacements_and_selected_policy_patches(tmp_path, method, request_type):
    async def scenario():
        gallery = Gallery()
        gallery.candidates = (UpdateCandidate("https://other.test/a", "a", "1"),)
        seen = []
        async with compose(tmp_path, gallery) as service:
            await attach_transport(service)
            original = service._operations.resolve_access

            def capture(plugin, url, overrides=None, replacements=None, patches=None):
                access = original(plugin, url, overrides, replacements, patches)
                seen.append((url, access.site_id, access.site.mode, access.site.login_policy))
                return access

            service._operations.resolve_access = capture
            replacements = {
                "core.generic-html": {
                    "identity": {"origins": ["https://example.test"], "credential_access": "deny", "login": "deny"}
                }
            }
            patch = {"core.generic-html": {"login": "allow", "session": {"persistence": "ephemeral"}}}
            options = {"workflow_retries": 0} if method == "workflow" else {}
            request = request_type(
                url=FEED, plugin_auth_access_replacements=replacements, plugin_auth_access_patches=patch, **options
            )
            await getattr(service, method)(request)
            assert seen and all(mode == "anonymous" and login == "allow" for _, _, mode, login in seen)
            assert any(url == FEED and binding == "identity" for url, binding, _, _ in seen)
            if method == "workflow":
                assert any(
                    url == "https://other.test/a" and binding == "https://other.test:443" for url, binding, _, _ in seen
                )
            assert not service.config.plugin_settings

    asyncio.run(scenario())
