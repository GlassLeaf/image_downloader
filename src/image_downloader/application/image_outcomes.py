"""Small internal result port independent of workflow coordination."""

from __future__ import annotations

from typing import Protocol

from ..models import ImageOutcome


class ImageOutcomeSink(Protocol):
    def retained_outcome(self, chapter_position: int, image_position: int) -> ImageOutcome | None: ...

    def mark_attempted(self, chapter_position: int, image_position: int) -> None: ...

    def record(self, chapter_position: int, image_position: int, outcome: ImageOutcome) -> None: ...


class NullImageOutcomeSink:
    def retained_outcome(self, chapter_position: int, image_position: int) -> ImageOutcome | None:
        return None

    def mark_attempted(self, chapter_position: int, image_position: int) -> None:
        pass

    def record(self, chapter_position: int, image_position: int, outcome: ImageOutcome) -> None:
        pass
