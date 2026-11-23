from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from image_downloader import Endpoint
from image_downloader.configuration import endpoint_origins
from image_downloader.configuration.endpoint_origins import OriginPattern, expand_origins
from image_downloader.configuration.models import EndpointSettings, PluginConnections
from image_downloader.exceptions import ConfigurationError
from image_downloader.transport.endpoint_rules import EndpointRules, expand_connections


def test_origin_arrays_are_normalized_deduplicated_and_immutable():
    settings = EndpointSettings(origin=["https://EXAMPLE.com/", "https://example.com:443"], auth="none")
    assert settings.origin == ("https://example.com:443",)
    assert EndpointSettings(origin="https://example.com", auth="none").origin == "https://example.com:443"
    assert settings.model_dump(mode="json")["origin"] == ["https://example.com:443"]
    with pytest.raises(ValueError):
        EndpointSettings(origin=[], auth="none")
    with pytest.raises(ValueError):
        EndpointSettings(origin=["https://example.com", True], auth="none")
    with pytest.raises(ValueError):
        EndpointSettings(origin={"https://example.com"}, auth="none")


@pytest.mark.parametrize(
    "origin",
    [
        "https://cdn*.example.com",
        "https://**.example.com",
        "https://*.*.example.com",
        "*://example.com",
        "https://example.com:*",
        "https://*.com",
        "https://*.co.jp",
        "https://*.github.io",
        "https://*.vercel.app",
        "https://*.pages.dev",
        "https://*.unknown-suffix",
        "https://*.127.0.0.1",
        "https://example.com/path",
        "https://user:password@example.com",
        "https://example.com?query",
        "https://example.com#fragment",
        "https://example.com:99999",
        "https://example.com\n",
        "https://example.com\\other",
        "https://${env.HOST}",
        "https://${secrets.host}",
        "https://${operation.unknown}",
        "https://${config.host",
        "https://${config.host[0]}",
    ],
)
def test_invalid_origins_and_variables_are_rejected(origin):
    with pytest.raises(ValueError):
        EndpointSettings(origin=origin, auth="session")


@pytest.mark.parametrize(
    ("target", "matches"),
    [
        ("https://cdn.example.com", True),
        ("https://CDN.EXAMPLE.com:443/", True),
        ("https://a.cdn.example.com", False),
        ("https://example.com", False),
        ("https://cdn.example.com.evil.com", False),
        ("https://cdn.notexample.com", False),
        ("http://cdn.example.com", False),
        ("https://cdn.example.com:8443", False),
    ],
)
def test_wildcard_matches_one_host_label_and_exact_scheme_and_port(target, matches):
    pattern = OriginPattern.parse("https://*.example.com")
    assert pattern.matches(OriginPattern.parse(target)) is matches


def test_idna_trailing_dot_default_port_and_ipv6_normalization():
    assert expand_origins("https://BÜCHER.de./", {}, None) == ("https://xn--bcher-kva.de:443",)
    assert expand_origins(
        ["${operation.origin}", "${operation.scheme}://${operation.host}:${operation.port}"],
        {},
        "https://[2001:db8::1]:8443/page",
    ) == ("https://[2001:db8::1]:8443",)
    pattern = OriginPattern.parse("https://*.bücher.de")
    assert pattern.matches(OriginPattern.parse("https://cdn.xn--bcher-kva.de"))


@pytest.mark.parametrize(
    ("url", "domain"),
    [
        ("https://www.host1.com/page", "host1.com"),
        ("https://www.example.co.jp/page", "example.co.jp"),
        ("https://reader.alice.github.io/page", "alice.github.io"),
        ("https://reader.alice.vercel.app/page", "alice.vercel.app"),
        ("https://reader.alice.pages.dev/page", "alice.pages.dev"),
    ],
)
def test_operation_site_domain_respects_public_and_private_suffixes(url, domain):
    assert expand_origins("https://cdn.${operation.site_domain}", {}, url) == (f"https://cdn.{domain}:443",)


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/page",
        "https://[::1]/page",
        "http://localhost/page",
        "https://www.host.test/page",
        "https://co.jp/page",
        "https://github.io/page",
    ],
)
def test_indeterminate_site_domain_fails_only_when_referenced(url):
    assert expand_origins("https://cdn.example.com", {}, url) == ("https://cdn.example.com:443",)
    with pytest.raises(ValueError, match="site_domain"):
        expand_origins("https://cdn.${operation.site_domain}", {}, url)


def test_psl_unavailable_never_uses_a_heuristic_fallback(monkeypatch):
    monkeypatch.setattr(endpoint_origins, "_suffix_list", lambda: None)
    assert expand_origins("${operation.origin}", {}, "https://www.example.com/page") == ("https://www.example.com:443",)
    with pytest.raises(ValueError, match="Public Suffix List"):
        expand_origins("https://cdn.${operation.site_domain}", {}, "https://www.example.com/page")
    with pytest.raises(ValueError, match="Public Suffix List"):
        OriginPattern.parse("https://*.example.com")


def test_nested_config_and_one_array_expand_and_deduplicate():
    assert expand_origins(
        ("${config.cdn.origins}", "${operation.scheme}://cdn.${config.hosts}:${operation.port}"),
        {
            "cdn": {"origins": ["https://cdn.host1.com:8443", "https://fixed.example.com"]},
            "hosts": ["host1.com", "host2.com", "host1.com"],
        },
        "https://www.host1.com:8443/page",
    ) == ("https://cdn.host1.com:8443", "https://fixed.example.com:443", "https://cdn.host2.com:8443")


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"hosts": []},
        {"hosts": True},
        {"hosts": 443},
        {"hosts": ["example.com", 1]},
        {"hosts": {"nested": "example.com"}},
        {"hosts": ""},
        {"hosts": "${operation.host}"},
        {"hosts": ["${config.other}"]},
    ],
)
def test_invalid_config_reference_fails_without_skipping_candidates(config):
    with pytest.raises(ValueError):
        expand_origins("https://cdn.${config.hosts}", config, "https://example.com/page")


def test_multiple_array_references_are_not_a_cartesian_product():
    with pytest.raises(ValueError, match="at most one array"):
        expand_origins(
            "https://${config.labels}.${config.hosts}",
            {"labels": ["cdn"], "hosts": ["example.com"]},
            "https://example.com/page",
        )


def test_doctor_expands_config_without_fabricating_an_operation_origin():
    connections = PluginConnections.model_validate(
        {"endpoints": {"images": {"origin": "https://${config.label}.${operation.site_domain}", "auth": "none"}}}
    )
    expanded = expand_connections(connections, {"label": "cdn"})
    assert expanded.endpoints["images"].origin == ("https://cdn.${operation.site_domain}",)
    with pytest.raises(ConfigurationError, match="endpoints.images.origin"):
        expand_connections(connections, {})


def test_expanded_alias_conflicts_are_rejected_before_use():
    connections = PluginConnections.model_validate(
        {
            "endpoints": {
                "first": {"origin": "${config.origins}", "auth": "session"},
                "second": {"origin": "https://cdn.example.com", "auth": "none"},
            }
        }
    )
    with pytest.raises(ConfigurationError, match="conflicting expanded"):
        EndpointRules(connections, {"origins": ["https://CDN.example.com/"]}, "https://example.com/page")


def test_conflicts_use_effective_verification_and_allow_specific_exceptions():
    with pytest.raises(ValueError, match="conflicting authentication"):
        PluginConnections.model_validate(
            {
                "authentication": {"verify_before_request": True},
                "endpoints": {
                    "one": {"origin": ["https://*.example.com"], "auth": "session"},
                    "two": {
                        "origin": "https://*.EXAMPLE.com:443",
                        "auth": "session",
                        "verification": {"before_request": False},
                    },
                },
            }
        )
    connections = PluginConnections.model_validate(
        {
            "authentication": {"verify_before_request": True},
            "endpoints": {
                "wild": {"origin": "https://*.example.com", "auth": "session"},
                "alias": {
                    "origin": ["https://*.example.com"],
                    "auth": "session",
                    "verification": {"before_request": True},
                },
                "public": {"origin": "https://public.example.com", "auth": "none"},
            },
        }
    )
    rules = EndpointRules(connections, {}, "https://example.com")
    assert rules.lookup("https://public.example.com:443").auth == "none"
    assert rules.lookup("https://PUBLIC.example.com/").auth == "none"
    assert rules.lookup("https://cdn.example.com:443").policy.verify_before_request is True


def test_endpoint_requires_explicit_selection_and_rejects_origin_escape():
    endpoint = Endpoint(("https://fixed.example.com", "https://*.images.example.com"))
    assert endpoint.origins == ("https://fixed.example.com:443", "https://*.images.example.com:443")
    with pytest.raises(ValueError, match="for_origin"):
        _ = endpoint.origin
    with pytest.raises(ValueError, match="for_origin"):
        endpoint.request("/image")
    selected = endpoint.for_origin("https://cdn.images.example.com")
    assert selected.request("/image").url == "https://cdn.images.example.com:443/image"
    for origin in (
        "https://example.org",
        "https://a.cdn.images.example.com",
        "https://*.images.example.com",
        "https://cdn.images.example.com/path",
        "https://cdn.images.example.com:8443",
    ):
        with pytest.raises(ValueError):
            endpoint.for_origin(origin)
    with pytest.raises(ValueError):
        selected.request("//other.example.com/image")
    with pytest.raises(FrozenInstanceError):
        endpoint.origins = ()
    assert Endpoint(("https://example.com", "https://EXAMPLE.com:443")).origin == "https://example.com:443"
