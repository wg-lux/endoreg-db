from __future__ import annotations

import pytest
from pydantic import ValidationError

from endoreg_db.schemas import (
    VideoAnnotationExportRequestPayload,
    dump_video_annotation_export_update_payload,
)


def test_video_annotation_export_payload_normalizes_compatibility_fields() -> None:
    payload = VideoAnnotationExportRequestPayload.model_validate(
        {
            "output_dir": "data/export",
            "format": "json",
            "export_videos": "false",
            "only_validated": "true",
            "transcode_frames": "false",
            "center_key": "  ",
        }
    )

    data = dump_video_annotation_export_update_payload(payload)

    assert data == {
        "output_dir": "data/export",
        "output_format": "json",
        "export_videos": False,
        "transcode_frames": False,
        "center_key": None,
        "only_validated": True,
    }


def test_video_annotation_export_payload_prefers_output_format() -> None:
    payload = VideoAnnotationExportRequestPayload.model_validate(
        {"format": "json", "output_format": "csv"}
    )

    data = dump_video_annotation_export_update_payload(payload)

    assert data == {"output_format": "csv"}


def test_video_annotation_export_payload_rejects_invalid_format() -> None:
    with pytest.raises(ValidationError):
        VideoAnnotationExportRequestPayload.model_validate({"format": "xlsx"})
