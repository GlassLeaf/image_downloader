"""In-memory image outcomes and retry policy for one workflow invocation."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from uuid import uuid4

from ..exceptions import ErrorInfo
from ..models import (
    AdditionalFileHookContext,
    AdditionalFileOutcome,
    ChapterResult,
    DownloadManifest,
    DownloadResult,
    ImageOutcome,
    ImageOutcomeKind,
    WorkflowImageResult,
)


def retryable(code: str, http_status: int | None = None) -> bool:
    if code == "http_status_error":
        return http_status in {401, 403, 408, 429} or (http_status is not None and 500 <= http_status < 600)
    return code in {
        "request_error",
        "http_transport_error",
        "authentication_error",
        "plugin_error",
        "image_processing_error",
        "image_decode_error",
        "image_content_type_error",
        "image_mime_mismatch",
        "image_worker_error",
    }


class ImageLedger:
    """Retain only unambiguous, unchanged image results, including fail-fast successes."""

    def __init__(self, source_url: str) -> None:
        self.source_url = source_url
        self.plugin_id: str | None = None
        self.manifest: DownloadManifest | None = None
        self.outcomes: dict[tuple[int, int], ImageOutcome] = {}
        self.retained: set[tuple[int, int]] = set()
        self.attempted: set[tuple[int, int]] = set()
        self.operation_id = uuid4().hex
        self.attempt_number = 0
        self.additional_files: list[AdditionalFileOutcome] = []
        self._additional_identities: dict[int, tuple[str, str]] = {}
        self._current_additional_identities: dict[tuple[int, int], tuple[str, str]] = {}

    def start_attempt(self) -> None:
        self.attempt_number += 1
        self.retained = set(self.outcomes)
        self.attempted = set()

    @staticmethod
    def _identities(manifest: DownloadManifest) -> dict[tuple[int, int], tuple[str, str]]:
        chapter_keys = ["id:" + c.chapter_id if c.chapter_id else f"number:{c.number}" for c in manifest.chapters]
        counts = Counter(chapter_keys)
        identities = {}
        for cp, chapter in enumerate(manifest.chapters):
            keys = ["id:" + i.image_id if i.image_id else "locator:" + i.url for i in chapter.images]
            image_counts = Counter(keys)
            for ip, key in enumerate(keys):
                if counts[chapter_keys[cp]] == 1 and image_counts[key] == 1:
                    identities[cp, ip] = (chapter_keys[cp], key)
        return identities

    def begin(self, manifest: DownloadManifest, plugin_id: str) -> None:
        old = {}
        if self.manifest is not None and self.plugin_id == plugin_id:
            for pos, identity in self._identities(self.manifest).items():
                if pos in self.outcomes:
                    old[identity] = self.outcomes[pos]
        self.manifest, self.plugin_id = manifest, plugin_id
        self.outcomes = {}
        self.retained = set()
        self.attempted = set()
        for pos, identity in self._identities(manifest).items():
            outcome = old.get(identity)
            image = manifest.chapters[pos[0]].images[pos[1]]
            if outcome is not None and outcome.image == image:
                if outcome.kind is not ImageOutcomeKind.FAILED or not self._retryable_outcome(outcome):
                    self.outcomes[pos] = replace(outcome, image=image)
                    self.retained.add(pos)
        identities = self._identities(manifest)
        self._current_additional_identities = {
            (id(manifest.chapters[cp]), id(manifest.chapters[cp].images[ip])): identity
            for (cp, ip), identity in identities.items()
        }
        retained = {identities[pos]: pos for pos in self.retained}
        kept = []
        associations = {}
        for item in self.additional_files:
            item_identity = self._additional_identities.get(id(item))
            if item.image_index is None or item.attempt_number == self.attempt_number:
                kept.append(item)
            elif item_identity is not None and item_identity in retained:
                cp, ip = retained[item_identity]
                item = replace(
                    item,
                    chapter_number=manifest.chapters[cp].number,
                    image_index=manifest.chapters[cp].images[ip].index,
                )
                kept.append(item)
            else:
                continue
            if item_identity is not None:
                associations[id(item)] = item_identity
        self.additional_files, self._additional_identities = kept, associations

    def record_additional(self, outcome: AdditionalFileOutcome, hook: AdditionalFileHookContext) -> None:
        self.additional_files.append(outcome)
        if hook.manifest is None or hook.chapter is None or hook.image is None:
            return
        identity = self._current_additional_identities.get((id(hook.chapter), id(hook.image)))
        if identity is not None:
            self._additional_identities[id(outcome)] = identity

    @staticmethod
    def _retryable_outcome(outcome: ImageOutcome) -> bool:
        failure = outcome.failure
        return failure is not None and retryable(failure.code, failure.http_status)

    def record(self, chapter_position: int, image_position: int, outcome: ImageOutcome) -> None:
        self.outcomes[chapter_position, image_position] = outcome

    def result(self) -> DownloadResult | None:
        if self.manifest is None:
            return None
        return DownloadResult(
            self.source_url,
            self.manifest,
            tuple(
                ChapterResult(
                    chapter,
                    tuple(self.outcomes[cp, ip] for ip in range(len(chapter.images)) if (cp, ip) in self.outcomes),
                )
                for cp, chapter in enumerate(self.manifest.chapters)
            ),
            tuple(self.additional_files),
        )

    def images(self) -> tuple[WorkflowImageResult, ...]:
        if self.manifest is None:
            return ()
        return tuple(
            WorkflowImageResult(
                cp + 1,
                ip + 1,
                image,
                self.outcomes[cp, ip].kind.value if (cp, ip) in self.outcomes else "unprocessed",
                (cp, ip) in self.retained,
                self.outcomes.get((cp, ip)),
                (cp, ip) in self.attempted,
            )
            for cp, chapter in enumerate(self.manifest.chapters)
            for ip, image in enumerate(chapter.images)
        )

    def needs_retry(self, error: ErrorInfo | None) -> bool:
        if error is not None and retryable(error.code, error.http_status):
            return True
        return any(
            i.status == "unprocessed" or (i.outcome is not None and self._retryable_outcome(i.outcome))
            for i in self.images()
        )
