from __future__ import annotations

from enum import Enum


class SegmentAnnotationStatus(str, Enum):
    NOT_STARTED = "not_started"
    CLEANUP_REQUIRED = "cleanup_required"
    CLEANUP_QUEUED = "cleanup_queued"
    CLEANUP_RUNNING = "cleanup_running"
    CLEANUP_FAILED = "cleanup_failed"
    VALIDATED = "validated"

    def __str__(self) -> str:
        return self.value
