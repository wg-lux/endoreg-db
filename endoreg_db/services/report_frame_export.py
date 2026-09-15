"""Materialize only a selected processed frame for the duration of PDF rendering."""

from __future__ import annotations

import math
import tempfile
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
from typing import cast

from endoreg_db.models.media.frame.frame import Frame
from endoreg_db.utils.file_operations import (
    atomic_write_file,
    ensure_directory,
    safe_rmtree,
)
from endoreg_db.utils.frame_stream import read_video_file_frame_jpeg


@contextmanager
def materialized_report_frame(frame: Frame) -> Generator[Path]:
    timestamp = frame.timestamp
    if timestamp is None or not math.isfinite(timestamp) or timestamp < 0:
        raise ValueError("Report frame requires a persisted presentation timestamp")
    video = frame.video
    state = video.state
    metadata: object = video.meta
    if (
        isinstance(metadata, Mapping)
        and str(
            cast(Mapping[str, object], metadata).get("integrity_status", "")
        ).lower()
        == "lost"
    ):
        raise ValueError("Report frames cannot use media marked LOST")
    if (
        state is None
        or not state.anonymized
        or not state.anonymization_validated
        or state.processing_error
    ):
        raise ValueError("Report frames require validated anonymized media")
    sample = read_video_file_frame_jpeg(
        video, frame_number=frame.frame_number, file_type="processed"
    )
    if sample.timestamp != timestamp:
        raise ValueError("Decoded frame timestamp does not match the selected frame")
    directory = Path(tempfile.gettempdir()) / f"endoreg-report-frame-{uuid4().hex}"
    try:
        ensure_directory(directory, dir_mode=0o700)
        path = atomic_write_file(
            destination=directory / "frame.jpg",
            content=[sample.image_bytes],
            file_mode=0o600,
            dir_mode=0o700,
        )
        yield path
    finally:
        safe_rmtree(directory, missing_ok=True)
