from __future__ import annotations
import os
from logging import getLogger
from typing import Any, TypeVar

from django.db import models

logger = getLogger(__name__)

_TEST_RUN_ENV = os.environ.get("TEST_RUN", "False")
TEST_RUN = _TEST_RUN_ENV.lower() == "true"


_M = TypeVar("_M", bound=models.Model)


class NameNaturalKeyManager(models.Manager[_M]):
    """Manager resolving fixture natural keys by the unique ``name`` field."""

    def get_by_natural_key(self, name: str) -> _M:
        return self.get(name=name)


def find_segments_in_prediction_array(prediction_array: Any, min_frame_len: int):
    """
    Expects a prediction array of shape (num_frames) and a minimum frame length.
    Returns a list of tuples (start_frame_number, end_frame_number) that represent the segments.
    """
    import numpy as np

    # Add False to the beginning and end to detect changes at the array boundaries
    padded_prediction = np.pad(
        prediction_array, (1, 1), "constant", constant_values=False
    )

    # Find the start points and end points of the segments
    diffs = np.diff(padded_prediction.astype(int))
    segment_starts = np.where(diffs == 1)[0]
    segment_ends = np.where(diffs == -1)[0]

    # Filter segments based on min_frame_len
    segments = [
        (start, end)
        for start, end in zip(segment_starts, segment_ends)
        if end - start >= min_frame_len
    ]

    return segments


__all__ = [
    "NameNaturalKeyManager",
    "find_segments_in_prediction_array",
    "TEST_RUN",
]
