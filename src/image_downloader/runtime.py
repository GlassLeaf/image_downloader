"""Public compatibility imports for download service composition."""

from .application.composer import RuntimeComposer
from .application.dependencies import _RuntimeDependencies as _RuntimeDependencies
from .application.service import DownloadService
from .credentials.plugin_secrets import RuntimeSecrets
from .media.artifact_pipeline import ArtifactPipeline
from .observability.chapter_reporter import ChapterReporter
from .output.output_allocator import OutputAllocation, OutputAllocator
from .storage.state import UpdateState
from .transport.gateway import RequestGateway

__all__ = [
    "ArtifactPipeline",
    "ChapterReporter",
    "DownloadService",
    "OutputAllocation",
    "OutputAllocator",
    "RequestGateway",
    "RuntimeComposer",
    "RuntimeSecrets",
    "UpdateState",
    "_RuntimeDependencies",
]
