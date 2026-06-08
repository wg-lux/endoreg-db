from __future__ import annotations

from datetime import datetime, timezone

import pytest
from lx_dtypes.models.contracts import (
    VideoExaminationCreatePayload,
    VideoExaminationFindingPayload,
    VideoExaminationUpdatePayload,
    dump_video_examination_create_payload,
    dump_video_examination_finding_payload,
    dump_video_examination_update_payload,
)
from pydantic import ValidationError


def test_video_examination_create_payload_preserves_unset_dates() -> None:
    payload = VideoExaminationCreatePayload.model_validate(
        {"video_id": 1, "examination_id": 2}
    )

    data = dump_video_examination_create_payload(payload)

    assert data == {"video_id": 1, "examination_id": 2}


def test_video_examination_update_payload_preserves_explicit_null_date() -> None:
    payload = VideoExaminationUpdatePayload.model_validate({"date_start": None})

    data = dump_video_examination_update_payload(payload)

    assert data == {"date_start": None}


def test_video_examination_payload_rejects_invalid_ids() -> None:
    with pytest.raises(ValidationError):
        VideoExaminationCreatePayload.model_validate(
            {"video_id": 0, "examination_id": 2}
        )


def test_video_examination_finding_payload_keeps_datetime_value() -> None:
    created_at = datetime(2026, 6, 3, 12, 30, tzinfo=timezone.utc)
    payload = VideoExaminationFindingPayload(
        id=1,
        finding_id=2,
        finding_name="polyp",
        created_at=created_at,
    )

    data = dump_video_examination_finding_payload(payload)

    assert data == {
        "id": 1,
        "finding_id": 2,
        "finding_name": "polyp",
        "created_at": created_at,
    }
