"""Explicit user grants for tests of authentication mechanics."""

from image_downloader.configuration.models import PluginAuthAccess, PluginConnections
from image_downloader.credentials.access import resolve_auth_access
from image_downloader.transport.endpoint_rules import EndpointRules
from image_downloader.transport.metadata import _request_origin


def approved_access(url="https://site.test", *, plugin_id="com.example.auth", origins=(), login="allow"):
    return resolve_auth_access(
        plugin_id,
        url,
        PluginAuthAccess.model_validate(
            {
                "sites": {
                    "test": {
                        "origins": list(dict.fromkeys([_request_origin(url), *origins])),
                        "login": login,
                    }
                }
            }
        ),
    )


def approved_operation(gateway, *, origins=(), login="allow", **kwargs):
    """Tests explicitly opt into exact origins while testing author endpoint mechanics."""
    url = kwargs["operation_url"]
    settings = kwargs.get("settings") or PluginConnections()
    connections = PluginConnections.model_validate(
        {"authentication": settings.authentication, "endpoints": settings.endpoints}
    )
    rules = EndpointRules(connections, kwargs.get("plugin_config", getattr(settings, "config", {})), url)
    declared = [origin for endpoint in rules.endpoints.values() for origin in endpoint.origins if "*" not in origin]
    kwargs.setdefault(
        "access",
        approved_access(
            url,
            plugin_id=kwargs.get("plugin_id") or "test.auth",
            origins=(*declared, *origins),
            login=login,
        ),
    )
    return gateway.operation(**kwargs)
