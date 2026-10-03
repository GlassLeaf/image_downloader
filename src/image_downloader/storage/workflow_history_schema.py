"""Strict schema for persisted summaries; image and manifest fields are forbidden."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Count = Annotated[int, Field(ge=0)]
Status = Literal["success", "partial", "failed", "unprocessed"]


class SummaryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class ErrorSummary(SummaryModel):
    code: str
    reason: str
    exception: str
    message: str
    response_url: str | None
    http_status: int | None
    output_path: str | None


class FailureGroup(SummaryModel):
    kind: str
    code: str | None
    http_status: int | None
    response_url: str | None
    output_path: str | None
    count: Count


class AdditionalFileSummary(SummaryModel):
    file_id: str
    point: Literal[
        "before_manifest",
        "after_manifest",
        "before_image_request",
        "after_image_request",
        "before_image_save",
        "after_image_save",
        "after_download",
    ]
    operation_id: str
    invocation_id: str
    attempt_number: Count
    chapter_number: int | None
    image_index: int | None
    phase: Literal["declaration", "hook", "receive", "save", "read", "received_callback", "saved_callback"]
    status: Literal["received", "saved", "skipped", "failed", "completed", "no_target"]
    path: str | None
    error: ErrorSummary | None


class AttemptSummary(SummaryModel):
    round_number: Count
    status: Status
    saved: Count
    skipped: Count
    failures: Count
    error: ErrorSummary | None
    failure_groups: list[FailureGroup]
    additional_files: list[AdditionalFileSummary] = Field(default_factory=list)


class ItemSummary(SummaryModel):
    url: str
    reasons: list[Literal["all", "added", "changed", "unfinished"]]
    status: Literal["success", "partial", "failed", "unprocessed", "removed"]
    saved: Count
    skipped: Count
    failures: Count
    error: ErrorSummary | None
    failure_groups: list[FailureGroup]
    additional_files: list[AdditionalFileSummary] = Field(default_factory=list)
    attempts: list[AttemptSummary]


class Counts(SummaryModel):
    success: Count
    partial: Count
    failed: Count
    unprocessed: Count


class FinalCounts(Counts):
    removed: Count


class ChangeSummary(SummaryModel):
    kind: Literal["added", "changed", "removed"]
    url: str
    content_id: str | None
    revision: str | None


class RoundSummary(SummaryModel):
    round_number: Count
    status: Literal["complete", "stopped"]
    checked_at: str | None
    candidate_count: Count
    selected_urls: list[str]
    removed_urls: list[str]
    changes: list[ChangeSummary]
    attempt_summary: Counts
    error: ErrorSummary | None


class HistoryDetails(SummaryModel):
    download_scope: Literal["all", "updated"]
    workflow_retries: Count
    workflow_retry_delay: Annotated[float, Field(ge=0)]
    workflow_retry_timeout: Annotated[float, Field(gt=0)] | None
    timed_out: bool
    cancelled: bool
    stop_error: ErrorSummary | None
    summary: FinalCounts
    items: list[ItemSummary]
    rounds: list[RoundSummary]
