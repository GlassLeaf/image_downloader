"""Select browser cookies for a request host without broadening their scope."""

from __future__ import annotations

from dataclasses import dataclass
from http.cookiejar import Cookie, CookieJar

from ..configuration.endpoint_origins import _suffix_list, site_domain
from ..configuration.hosts import normalize_host
from ..exceptions import ConfigurationError


@dataclass(frozen=True, slots=True)
class BrowserCookieSelection:
    host: str
    query_host: str
    boundary: str | None
    is_ip: bool

    @classmethod
    def for_host(cls, value: str) -> BrowserCookieSelection:
        host, is_ip = normalize_host(value)
        try:
            boundary = None if is_ip else site_domain(host)
        except ValueError:
            boundary = None
        return cls(host, boundary or host, boundary, is_ip)

    def accepts(self, cookie: Cookie) -> bool:
        if not isinstance(cookie.domain, str):
            return False
        domain = cookie.domain.removeprefix(".")
        try:
            domain, is_ip = normalize_host(domain)
        except ConfigurationError:
            return False
        if not cookie.domain_specified or self.is_ip or is_ip:
            return domain == self.host
        psl = _suffix_list()
        if psl is not None and psl.get_tld(domain, strict=True) == domain:
            return False
        if domain == self.host:
            return True
        return bool(
            self.boundary
            and (domain == self.boundary or domain.endswith("." + self.boundary))
            and self.host.endswith("." + domain)
        )

    def select(self, loaded: CookieJar) -> CookieJar:
        result = CookieJar()
        for cookie in loaded:
            if self.accepts(cookie):
                result.set_cookie(cookie)
        return result
