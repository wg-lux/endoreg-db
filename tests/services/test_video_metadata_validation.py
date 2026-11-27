"""A failed identity correction must not approve media or schedule deletion."""

from unittest.mock import patch
from datetime import timedelta

import pytest
from django.core.files.base import ContentFile
from django.db import connection, transaction
from django.test import RequestFactory
from django.utils import timezone
from lx_dtypes.models.contracts.video_text_metadata import VideoTextMetaPayload

from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.models.media.operation_lease import MediaOperationLease
from endoreg_db.models.operation_log import OperationLog
from endoreg_db.services.video_storage import generation_cleanup
from endoreg_db.utils.operation_log import record_operation
from endoreg_db.services.video_files.validation import (
    validate_video_metadata_annotation,
)
from tests.helpers.default_objects import get_default_center


pytestmark = pytest.mark.django_db


@pytest.fixture
def video(base_db_data: object) -> VideoFile:
    center = get_default_center()
    sensitive_meta = SensitiveMeta.create_from_dict({"center": center})
    video = VideoFile.objects.create(center=center, sensitive_meta=sensitive_meta)
    video.get_or_create_state()
    return video


@pytest.mark.parametrize("error_type", [ValueError, RuntimeError])
def test_metadata_failure_cannot_fall_back_to_existing_identity(
    video: VideoFile, error_type: type[Exception]
) -> None:
    error = error_type("identity correction rejected")
    with (
        patch(
            "endoreg_db.services.video_files.metadata.update_video_text_metadata",
            side_effect=error,
        ),
        patch.object(SensitiveMeta, "update_from_dict") as fallback,
        patch(
            "endoreg_db.services.video_storage.generation_cleanup.cleanup_validated_raw_video"
        ) as cleanup,
    ):
        with pytest.raises(error_type, match="identity correction rejected") as raised:
            validate_video_metadata_annotation(
                video, VideoTextMetaPayload.model_validate({})
            )
        assert raised.value is error
        fallback.assert_not_called()
        cleanup.assert_not_called()

    video.refresh_from_db()
    assert video.state is not None
    assert not video.state.anonymization_validated
    assert not video.state.ready_for_export


@pytest.mark.parametrize("supplied_payload", [False, True])
def test_missing_metadata_result_cannot_approve_existing_identity(
    video: VideoFile, supplied_payload: bool
) -> None:
    payload = VideoTextMetaPayload.model_validate({}) if supplied_payload else None
    with (
        patch(
            "endoreg_db.services.video_files.metadata.update_video_text_metadata",
            return_value=None,
        ),
        patch(
            "endoreg_db.services.video_storage.generation_cleanup.cleanup_validated_raw_video"
        ) as cleanup,
    ):
        assert validate_video_metadata_annotation(video, payload) is False
        cleanup.assert_not_called()

    video.refresh_from_db()
    assert video.state is not None
    assert not video.state.anonymization_validated


@pytest.mark.parametrize("include_center", [False, True])
def test_model_validation_preserves_typed_payload_and_supplies_center(
    video: VideoFile,
    include_center: bool,
) -> None:
    video.sensitive_meta = None
    video.save(update_fields=["sensitive_meta"])
    values = {
        "patient_first_name": "Max",
        "patient_last_name": "Mustermann",
        "patient_dob": "1994-03-21",
        "examination_date": "2024-02-15",
    }
    if include_center:
        values["center_name"] = video.center.name
    payload = VideoTextMetaPayload.model_validate(values)

    assert video.validate_metadata_annotation(payload) is True

    video.refresh_from_db()
    assert video.sensitive_meta is not None
    assert video.sensitive_meta.center == video.center
    assert video.sensitive_meta.patient_first_name == "Max"
    assert video.state is not None
    assert video.state.anonymization_validated
    assert ("center_name" in payload.root) is include_center


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("active_lease", [False, True])
def test_raw_cleanup_runs_after_validation_and_audit_commit(
    video: VideoFile, monkeypatch: pytest.MonkeyPatch, active_lease: bool
) -> None:
    raw_bytes = b"retained raw video"
    video.raw_file.save("post-commit-raw.mp4", ContentFile(raw_bytes))
    raw_name = video.raw_file.name
    assert raw_name is not None
    if active_lease:
        MediaOperationLease.objects.create(
            video=video,
            lease_type=MediaOperationLease.LEASE_STREAM,
            expires_at=timezone.now() + timedelta(minutes=5),
        )
    cleanup_results: list[tuple[str, ...]] = []
    original_cleanup = generation_cleanup.cleanup_validated_raw_video

    def observed_cleanup(video_id: int, *, apply: bool = False) -> tuple[str, ...]:
        assert connection.get_autocommit()
        assert OperationLog.objects.filter(
            action="anonymization.validated", resource_id=video_id
        ).exists()
        result = original_cleanup(video_id, apply=apply)
        cleanup_results.append(result)
        return result

    monkeypatch.setattr(
        generation_cleanup, "cleanup_validated_raw_video", observed_cleanup
    )
    payload = VideoTextMetaPayload.model_validate(
        {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "1994-03-21",
            "examination_date": "2024-02-15",
        }
    )
    with transaction.atomic():
        assert validate_video_metadata_annotation(video, payload)
        record_operation(
            RequestFactory().post("/anonymization/validate/"),
            action="anonymization.validated",
            resource_type="video",
            resource_id=video.pk,
        )
        assert cleanup_results == []
        assert video.raw_file.storage.exists(raw_name)
    assert len(cleanup_results) == 1
    if active_lease:
        assert cleanup_results == [("active_media_operation_lease",)]
    else:
        assert "storage_normalization_evidence_missing" in cleanup_results[0]
    video.refresh_from_db()
    assert video.raw_file.name == raw_name
    with video.raw_file.open("rb") as raw:
        assert raw.read() == raw_bytes


@pytest.mark.parametrize("failed", [False, True])
def test_lost_or_failed_video_cannot_release_validated_raw_source(failed: bool) -> None:
    from uuid import uuid4
    from endoreg_db.models import Center, VideoState
    from tests.helpers.media_states import create_media_state

    center = Center.objects.create(name=uuid4().hex)
    video = VideoFile.objects.create(
        center=center,
        raw_video_hash=uuid4().hex,
        meta={} if failed else {"integrity_status": "lost"},
        state=create_media_state(
            VideoState, anonymization_validated=True, processing_error=failed
        ),
    )
    assert generation_cleanup.cleanup_validated_raw_video(video.pk, apply=True) == (
        "media_failed_or_lost",
    )
