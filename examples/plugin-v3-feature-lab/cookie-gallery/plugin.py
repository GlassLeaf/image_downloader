from .base import BaseGallery


class LabPlugin(BaseGallery):
    """The core's cookie jar supplies authentication; no browser access here."""

    slug = "cookie-gallery"
