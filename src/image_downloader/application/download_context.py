"""Explicit, operation-owned inputs to chapter and image execution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ..media.artifact_pipeline import ArtifactPipeline
from ..models import (
    DownloadManifest,
)
from ..output.output_allocator import OutputAllocator
from .additional_files import AdditionalFiles
from .image_outcomes import ImageOutcomeSink, NullImageOutcomeSink
from .site_operation import SiteOperation


@dataclass(frozen=True, slots=True)
class DownloadContext:
    """Prepared dependencies shared by chapters of one download."""

    site: SiteOperation
    manifest: DownloadManifest
    allocator: OutputAllocator
    pipeline: ArtifactPipeline
    source_url: str
    plugin_values: Mapping[str, Mapping[str, str]]
    image_outcomes: ImageOutcomeSink = field(default_factory=NullImageOutcomeSink)
    additional: AdditionalFiles | None = None
    unexpected_errors_are_fatal: bool = False
