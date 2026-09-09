from __future__ import annotations

import pytest
from pydantic import ValidationError

from endoreg_db.schemas import (
    AIDataSetExportRequestPayload,
    dump_ai_dataset_export_request_payload,
    dump_ai_dataset_export_summary,
)


def test_ai_dataset_export_request_normalizes_legacy_transport_values() -> None:
    payload = AIDataSetExportRequestPayload.model_validate(
        {
            "dataset_id": "12",
            "center_key": " center-a ",
            "all_centers": "false",
            "only_validated": "true",
        }
    )

    assert dump_ai_dataset_export_request_payload(payload) == {
        "schema_version": "1.0",
        "dataset_id": 12,
        "center_key": "center-a",
        "all_centers": False,
        "only_validated": True,
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"dataset_id": True},
        {"dataset_id": 0},
        {"only_validated": "sometimes"},
        {"unknown_field": "value"},
        {"schema_version": "2.0"},
    ],
)
def test_ai_dataset_export_request_rejects_invalid_input(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        AIDataSetExportRequestPayload.model_validate(payload)


def test_ai_dataset_export_summary_uses_shared_canonical_contract() -> None:
    assert dump_ai_dataset_export_summary({"image_annotation_count": 2}) == {
        "image_annotation_count": 2,
        "video_annotation_count": 0,
        "frame_count": 0,
        "video_count": 0,
        "label_count": 0,
    }


def test_ai_dataset_export_summary_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        dump_ai_dataset_export_summary({"image_annotation_count": 2, "other": 1})
