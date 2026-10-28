from __future__ import annotations

from uuid import uuid4

import pytest
from lx_dtypes.models.contracts.video_provenance import RecordingTimeRange
from pydantic import ValidationError

from endoreg_db.models import Center, SensitiveMeta, VideoFile
from endoreg_db.schemas.persisted_json import VideoFileMetaPayload
from endoreg_db.schemas.video_storage import (
    VideoExportFileName,
    VideoRecordingTimeEvidence,
)
from endoreg_db.services.media.export_file_names import (
    decide_export_file_name,
    file_name_contains_patient_name,
    persist_validated_export_file_name,
    resolve_export_file_name,
)

RECORDING_HASH = "recording-hash"


def _recording_time(
    started_at: str | None = "2024-02-15T09:30:05+00:00",
    *,
    recording_hash: str = RECORDING_HASH,
) -> VideoRecordingTimeEvidence:
    return VideoRecordingTimeEvidence(
        recording_hash=recording_hash,
        source="container_creation_time",
        recording=RecordingTimeRange(recording_started_at=started_at),
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("file_name", "first_name", "last_name"),
    [
        ("Mustermann_Max_coloscopy.mp4", "Max", "Mustermann"),
        ("mustermann.mp4", None, "Mustermann"),
        ("MUELLER-2024.mp4", "Hans", "Müller"),
        ("müller_video.mp4", "Hans", "Mueller"),
        ("Jose_Garcia.mp4", "José", "García"),
        ("video_schmidtpeter.mp4", "Anna-Maria", "Schmidt"),
        ("maria.mp4", "Anna-Maria", "Schmidt"),
    ],
)
def test_patient_name_is_detected_across_case_accents_and_transliteration(
    file_name: str, first_name: str | None, last_name: str | None
) -> None:
    assert file_name_contains_patient_name(
        file_name, first_name=first_name, last_name=last_name
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("file_name", "first_name", "last_name"),
    [
        ("2024-02-15_09-30-05.mp4", "Max", "Mustermann"),
        ("colonoscopy_room2.mp4", "Max", "Mustermann"),
        ("anything.mp4", None, None),
        ("anything.mp4", "", ""),
        # Single-character name parts are too unspecific to match.
        ("a_video.mp4", "A", None),
        # Only the file name is checked, never directories.
        ("x.mp4", "Max", "Mustermann"),
    ],
)
def test_file_names_without_patient_names_are_not_flagged(
    file_name: str, first_name: str | None, last_name: str | None
) -> None:
    assert not file_name_contains_patient_name(
        file_name, first_name=first_name, last_name=last_name
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("original_file_name", "expected"),
    [
        ("2024-02-15_09-30-05.mp4", "2024-02-15_09-30-05.mp4"),
        ("room2 capture.MOV", "room2 capture.mp4"),
        ("../../etc/room2.mp4", "room2.mp4"),
        ("C:\\exports\\room2.mp4", "room2.mp4"),
        ("room:2?.mp4", "room_2_.mp4"),
    ],
)
def test_original_file_name_is_kept_as_plain_mp4_name(
    original_file_name: str, expected: str
) -> None:
    decision = decide_export_file_name(
        recording_hash=RECORDING_HASH,
        original_file_name=original_file_name,
        first_name="Max",
        last_name="Mustermann",
        recording_time=_recording_time(),
    )

    assert decision == VideoExportFileName(
        file_name=expected, source="original_file_name", recording_hash=RECORDING_HASH
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("started_at", "expected"),
    [
        ("2024-02-15T09:30:05+00:00", "2024-02-15_09-30-05.mp4"),
        # The persisted offset is retained in the name.
        ("2024-02-15T10:30:05+01:00", "2024-02-15_10-30-05.mp4"),
    ],
)
def test_identifying_file_name_is_replaced_by_recording_time(
    started_at: str, expected: str
) -> None:
    decision = decide_export_file_name(
        recording_hash=RECORDING_HASH,
        original_file_name="Mustermann_Max.mp4",
        first_name="Max",
        last_name="Mustermann",
        recording_time=_recording_time(started_at),
    )

    assert decision.file_name == expected
    assert decision.source == "recording_started_at"
    assert "mustermann" not in decision.file_name.casefold()


@pytest.mark.unit
@pytest.mark.parametrize(
    "recording_time",
    [
        None,
        _recording_time(None),
        _recording_time(recording_hash="other-recording"),
    ],
)
def test_identifying_file_name_without_usable_recording_time_uses_hash(
    recording_time: VideoRecordingTimeEvidence | None,
) -> None:
    decision = decide_export_file_name(
        recording_hash=RECORDING_HASH,
        original_file_name="Mustermann_Max.mp4",
        first_name="Max",
        last_name="Mustermann",
        recording_time=recording_time,
    )

    assert decision.file_name == f"{RECORDING_HASH}.mp4"
    assert decision.source == "raw_video_hash"


@pytest.mark.unit
@pytest.mark.parametrize("original_file_name", [None, "", ".mp4", "   .mp4"])
def test_missing_or_unusable_original_name_uses_recording_time(
    original_file_name: str | None,
) -> None:
    decision = decide_export_file_name(
        recording_hash=RECORDING_HASH,
        original_file_name=original_file_name,
        first_name=None,
        last_name=None,
        recording_time=_recording_time(),
    )

    assert decision.file_name == "2024-02-15_09-30-05.mp4"
    assert decision.source == "recording_started_at"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("first_name", "last_name"),
    [(None, None), ("", ""), (" ", " "), ("Max", None), (None, "Mustermann")],
)
@pytest.mark.parametrize("has_recording_time", [True, False])
def test_original_name_without_complete_patient_names_uses_anonymous_fallback(
    first_name: str | None, last_name: str | None, has_recording_time: bool
) -> None:
    decision = decide_export_file_name(
        recording_hash=RECORDING_HASH,
        original_file_name="Mustermann_Max.mp4",
        first_name=first_name,
        last_name=last_name,
        recording_time=_recording_time() if has_recording_time else None,
    )

    assert decision.file_name == (
        "2024-02-15_09-30-05.mp4" if has_recording_time else f"{RECORDING_HASH}.mp4"
    )
    assert decision.source == (
        "recording_started_at" if has_recording_time else "raw_video_hash"
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "file_name", ["", " padded.mp4", ".hidden.mp4", "dir/name.mp4", "dir\\name.mp4"]
)
def test_export_file_name_schema_rejects_non_plain_names(file_name: str) -> None:
    with pytest.raises(ValidationError):
        VideoExportFileName(
            file_name=file_name,
            source="original_file_name",
            recording_hash=RECORDING_HASH,
        )


@pytest.mark.django_db
def test_validation_persists_identifier_free_export_name() -> None:
    center = Center.objects.create(name=f"export-name-{uuid4().hex[:8]}")
    recording_hash = f"export-name-{uuid4().hex}"
    video = VideoFile.objects.create(
        center=center,
        raw_video_hash=recording_hash,
        original_file_name="Mustermann_Max.mp4",
        sensitive_meta=SensitiveMeta.objects.create(
            center=center, patient_first_name="Max", patient_last_name="Mustermann"
        ),
        meta={
            "recording_time": _recording_time(recording_hash=recording_hash).model_dump(
                mode="json"
            )
        },
    )

    decision = persist_validated_export_file_name(video)

    video.refresh_from_db()
    persisted = VideoFileMetaPayload.model_validate(video.meta).export_file_name
    assert decision == persisted
    assert persisted == VideoExportFileName(
        file_name="2024-02-15_09-30-05.mp4",
        source="recording_started_at",
        recording_hash=recording_hash,
    )
    SensitiveMeta.objects.filter(pk=video.sensitive_meta_id).update(
        patient_first_name=None, patient_last_name=None
    )
    video.refresh_from_db()
    assert resolve_export_file_name(video) == persisted


@pytest.mark.django_db
@pytest.mark.parametrize("has_sensitive_meta", [True, False])
def test_legacy_video_without_patient_names_never_exports_original_name(
    has_sensitive_meta: bool,
) -> None:
    center = Center.objects.create(name=f"export-name-{uuid4().hex[:8]}")
    video = VideoFile.objects.create(
        center=center,
        raw_video_hash=f"export-name-{uuid4().hex}",
        original_file_name="Mustermann_Max.mp4",
        sensitive_meta=(
            SensitiveMeta.objects.create(center=center) if has_sensitive_meta else None
        ),
    )

    decision = resolve_export_file_name(video)

    assert decision.source == "raw_video_hash"
    assert decision.file_name == f"{video.raw_video_hash}.mp4"


@pytest.mark.django_db
def test_validation_without_recording_identity_skips_and_export_fails() -> None:
    video = VideoFile.objects.create(
        center=Center.objects.create(name=f"export-name-{uuid4().hex[:8]}"),
        raw_video_hash="",
        original_file_name="room2.mp4",
    )

    assert persist_validated_export_file_name(video) is None
    video.refresh_from_db()
    assert (
        VideoFileMetaPayload.model_validate(video.meta or {}).export_file_name is None
    )
    with pytest.raises(ValueError, match="no recording identity"):
        resolve_export_file_name(video)
