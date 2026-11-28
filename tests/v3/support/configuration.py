"""Build v2 file fixtures from runtime API values used by unrelated CLI tests."""

from copy import deepcopy
from pathlib import Path

import yaml

from image_downloader.configuration.models import SiteAuthAccess


def write_config(path: Path, value: dict) -> None:
    app = deepcopy(value)
    auth = {}
    if "auth_refresh_attempts" in app.get("network", {}):
        auth["execution"] = {"auth_refresh_attempts": app["network"].pop("auth_refresh_attempts")}
    email = app.get("notification", {}).get("email", {})
    if "credential_reference" in email:
        auth["smtp"] = {"credential_reference": email.pop("credential_reference")}
    email.pop("username", None)
    email.pop("credential_service", None)
    for plugin_id, settings in app.pop("plugin_settings", {}).items():
        private = {key: item for key, item in settings.items() if key in {"enabled", "config", "download_policy"}}
        _write(path.parent / "plugin-settings" / (plugin_id + ".yaml"), private)
        policy = {}
        connections = {
            key: item for key, item in settings.items() if key in {"host_groups", "authentication", "endpoints"}
        }
        if connections:
            policy["connections"] = connections
        access = settings.get("auth_access", {})
        if "mode" in access:
            policy["defaults"] = {
                "session": {"persistence": "ephemeral" if access["mode"] == "anonymous" else "persistent"}
            }
        if access.get("sites"):
            policy["host_groups"] = deepcopy(access.get("host_groups", settings.get("host_groups", {})))
            policy["bindings"] = {}
            for name, raw in access["sites"].items():
                binding = SiteAuthAccess.model_validate(raw)
                data = {
                    "origins": list(binding.origins),
                    "credentials": dict(binding.secrets),
                    "credential_access": "allow" if binding.provides_credentials else "deny",
                    "login": binding.login_policy,
                }
                if "mode" in raw:
                    data["session"] = {"persistence": "ephemeral" if binding.mode == "anonymous" else "persistent"}
                policy["bindings"][name] = data
        if policy:
            auth.setdefault("plugins", {})[plugin_id] = policy
    _write(path, {"config_version": 2, **app})
    if auth:
        _write(path.with_name("auth.yaml"), auth)


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
