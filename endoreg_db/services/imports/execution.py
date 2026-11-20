"""Shared execution authority; ownership and renewal remain domain responsibilities."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class ImportExecutionFence:
    """Check ownership between stages and hold it through short database mutations."""

    attempt_id: str
    guard: Callable[[], None]
    mutation_guard: Callable[[], AbstractContextManager[None]]

    def __post_init__(self) -> None:
        if re.fullmatch(r"[0-9a-f]{32}", self.attempt_id) is None:
            raise ValueError(
                "Import execution fence requires a lowercase UUID hex attempt_id"
            )
        if not callable(self.guard) or not callable(self.mutation_guard):
            raise TypeError("Import execution fence requires callable ownership guards")
