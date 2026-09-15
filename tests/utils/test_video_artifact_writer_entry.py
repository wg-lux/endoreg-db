from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from django.core.files.base import ContentFile
from django.db import connection

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.import_files.file_storage import state_management
from endoreg_db.models import Center, VideoFile
from endoreg_db.models.media.operation_lease import MediaOperationLease
from endoreg_db.services.media_operation_gate import (
    MediaOperationDeferred,
    acquire_video_transcode_lease,
    release_video_transcode_lease,
)
from endoreg_db.services.video_files import _anonymization

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def video() -> VideoFile:
    center = Center.objects.create(name=f"writer-{uuid4().hex}")
    return VideoFile.objects.create(center=center, video_hash=uuid4().hex)


@pytest.mark.parametrize("operation", ["anonymize", "finalize"])
def test_entry_rejects_active_transcode_before_work(
    video: VideoFile, tmp_path: Path, operation: str
) -> None:
    claim = acquire_video_transcode_lease(video_id=video.pk)
    try:
        with pytest.raises(MediaOperationDeferred):
            if operation == "anonymize":
                video.anonymize()
            else:
                context = ImportContext(
                    file_path=tmp_path / "input.mp4",
                    center_name=video.center.name,
                    file_type="video",
                )
                context.current_video = video
                state_management.finalize_video_success(context)
    finally:
        release_video_transcode_lease(claim)


@pytest.mark.parametrize("operation", ["anonymize", "finalize"])
def test_entry_holds_writer_before_transaction_and_releases_on_failure(
    video: VideoFile, tmp_path: Path, operation: str
) -> None:
    def fail_owned(*args: object, **kwargs: object) -> None:
        assert not connection.in_atomic_block
        assert (
            MediaOperationLease.objects.filter(
                video=video, lease_type=MediaOperationLease.LEASE_ARTIFACT_WRITE
            ).count()
            == 1
        )
        with pytest.raises(MediaOperationDeferred):
            acquire_video_transcode_lease(video_id=video.pk)
        raise RuntimeError("loader failed")

    module = _anonymization if operation == "anonymize" else state_management
    helper = (
        "_anonymize_owned"
        if operation == "anonymize"
        else "_finalize_video_success_owned"
    )
    with patch.object(module, helper, side_effect=fail_owned):
        with pytest.raises(RuntimeError, match="loader failed"):
            if operation == "anonymize":
                video.anonymize()
            else:
                context = ImportContext(
                    file_path=tmp_path / "input.mp4",
                    center_name=video.center.name,
                    file_type="video",
                )
                context.current_video = video
                state_management.finalize_video_success(context)
    assert not MediaOperationLease.objects.filter(video=video).exists()


@pytest.mark.parametrize("field_name", ["raw_file", "processed_file"])
@pytest.mark.parametrize("operation", ["save", "delete"])
def test_direct_file_mutation_is_rejected_before_storage(
    video: VideoFile, field_name: str, operation: str
) -> None:
    field = getattr(video, field_name)
    field.name = "existing.mp4"
    storage = Mock()
    field.storage = storage
    claim = acquire_video_transcode_lease(video_id=video.pk)
    try:
        with pytest.raises(MediaOperationDeferred):
            if operation == "save":
                field.save("candidate.mp4", ContentFile(b"candidate"), save=False)
            else:
                field.delete(save=False)
        storage.save.assert_not_called()
        storage.delete.assert_not_called()
    finally:
        release_video_transcode_lease(claim)
