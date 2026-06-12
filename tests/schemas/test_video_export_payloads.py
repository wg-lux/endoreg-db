from __future__ import annotations

import pytest
from pydantic import ValidationError

from endoreg_db.schemas import (
    VideoAnnotationExportErrorPayload,
    VideoAnnotationExportRequestPayload,
    VideoAnnotationExportResultPayload,
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


def test_video_annotation_export_result_payload_accepts_counts() -> None:
    payload = VideoAnnotationExportResultPayload.model_validate(
        {
            "success": True,
            "output_path": "exports/annotations.csv",
            "row_count": 12,
            "exported_video_count": 2,
            "exported_frame_count": 10,
        }
    )

    assert payload.row_count == 12
    assert payload.video_output_dir is None


def test_video_annotation_export_result_payload_rejects_negative_counts() -> None:
    with pytest.raises(ValidationError):
        VideoAnnotationExportResultPayload.model_validate(
            {
                "success": True,
                "output_path": "exports/annotations.csv",
                "row_count": -1,
                "exported_video_count": 0,
                "exported_frame_count": 0,
            }
        )


def test_video_annotation_export_error_payload_rejects_blank_error() -> None:
    with pytest.raises(ValidationError):
        VideoAnnotationExportErrorPayload.model_validate(
            {"success": False, "error": ""}
        )


def test_video_annotation_export_error_payload_rejects_extra_keys() -> None:
    with pytest.raises(ValidationError):
        VideoAnnotationExportErrorPayload.model_validate(
            {"success": False, "error": "failed", "detail": "extra"}
        )
