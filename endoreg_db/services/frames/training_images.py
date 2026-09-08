"""Read validated processed training frames without requiring a raw-video cache."""

from __future__ import annotations

from io import BytesIO
from collections.abc import Mapping
from typing import cast

from PIL import Image

from endoreg_db.models.media.frame.frame import Frame
from endoreg_db.utils.encryption.storage_materialization import (
    materialized_plaintext_field_file,
)
from endoreg_db.utils.frame_stream import read_video_path_frame_jpeg


def read_processed_training_image(frame: Frame) -> Image.Image:
    video = frame.video
    state = video.state
    metadata: object = video.meta
    integrity_status = (
        str(cast(Mapping[str, object], metadata).get("integrity_status", "")).lower()
        if isinstance(metadata, Mapping)
        else ""
    )
    if (
        state is None
        or not state.anonymized
        or not state.anonymization_validated
        or not state.segment_annotations_validated
        or not state.outside_segments_removed
        or not state.ready_for_export
        or state.processing_error
        or integrity_status == "lost"
        or not video.processed_file.name
    ):
        raise ValueError("Training requires validated, export-ready processed video.")
    with materialized_plaintext_field_file(video.processed_file, suffix=".mp4") as path:
        sample = read_video_path_frame_jpeg(
            path,
            frame_number=frame.frame_number,
            timestamp=frame.timestamp,
        )
        with Image.open(BytesIO(sample.image_bytes)) as image:
            return image.convert("RGB")
