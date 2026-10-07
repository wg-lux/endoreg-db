"""A failed identity correction must not approve media or schedule deletion."""

from unittest.mock import patch

import pytest
from lx_dtypes.models.contracts.video_text_metadata import VideoTextMetaPayload

from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
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
