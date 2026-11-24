"""Settle output reservations and publish their results before cancellation escapes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Literal

from ..storage.transactions import finish_task
from .output_allocator import OutputAllocation, OutputAllocator, OutputFormatContext
from .output_lock import OutputDirectoryLocks


@dataclass(frozen=True, slots=True)
class SaveReceipt:
    relative_path: Path
    status: Literal["saved", "skipped"]


class OutputWriter:
    def __init__(self, allocator: OutputAllocator, locks: OutputDirectoryLocks) -> None:
        self.allocator, self.locks = allocator, locks

    async def image(
        self,
        directory: Path,
        context: OutputFormatContext,
        data: bytes,
        on_settled: Callable[[SaveReceipt], None],
    ) -> SaveReceipt:
        return await self._publish(
            directory, directory, partial(self.allocator.allocate, directory, context), data, on_settled
        )

    async def additional(
        self,
        directory: Path,
        relative: Path,
        data: bytes,
        on_settled: Callable[[SaveReceipt], None],
    ) -> SaveReceipt:
        self.allocator.filesystem.ensure_directory(relative.parent)
        return await self._publish(
            directory, relative.parent, partial(self.allocator.allocate_relative, relative), data, on_settled
        )

    async def _publish(
        self,
        directory: Path,
        parent: Path,
        allocate: Callable[[], Awaitable[OutputAllocation]],
        data: bytes,
        on_settled: Callable[[SaveReceipt], None],
    ) -> SaveReceipt:
        async with self.locks.hold(self.allocator.filesystem.path(directory)):
            await self.allocator.refresh_directory(parent)
            allocation = await allocate()
            receipt = SaveReceipt(allocation.relative_path, "saved" if allocation.should_write else "skipped")
            if not allocation.should_write:
                on_settled(receipt)
                return receipt
            try:
                self.allocator.filesystem.write_bytes_atomic(allocation.relative_path, data)
            except BaseException:
                await finish_task(allocation.abort())
                raise

            async def commit() -> None:
                await allocation.commit()
                on_settled(receipt)

            await finish_task(commit())
            return receipt
