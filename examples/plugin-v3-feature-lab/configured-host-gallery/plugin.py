from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlsplit

from .base import BaseGallery, local_origin


class LabPlugin(BaseGallery):
    slug = "configured-host-gallery"

    def matches_with_config(self, url: str, config: Mapping[str, object], app_settings: Mapping[str, object]) -> bool:
        try:
            origin = urlsplit(local_origin(config.get("origin")))
            parsed = urlsplit(url)
            return (parsed.scheme, parsed.netloc) == (origin.scheme, origin.netloc) and parsed.path.startswith(
                f"/samples/{self.slug}/"
            )
        except (TypeError, ValueError):
            return False
