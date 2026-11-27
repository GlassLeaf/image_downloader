"""Task-local cookie sharing boundary used by preview and HTTP transport."""

from contextvars import ContextVar

from ..configuration.auth_origins import OriginMatcher

COOKIE_ORIGINS: ContextVar[OriginMatcher | None] = ContextVar("cookie_origins", default=None)
