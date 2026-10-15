"""Persistence invariants remain available without the consuming application."""

from typing import cast

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from lx_dtypes.models.contracts.json_types import JsonObject

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.aidataset.ai_dataset_split_plan import AIDatasetSplitPlan
from endoreg_db.models.aidataset.aidataset import AIDataSet
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.media.video.video_transcode_job import VideoTranscodeJob
from endoreg_db.schemas.ai_dataset_splits import SplitSnapshot

pytestmark = pytest.mark.django_db


@pytest.fixture
def snapshot() -> JsonObject:
    return cast(
        JsonObject,
        {
            "config": {"name": "Partition", "k": 2, "test_percent": 20, "seed": 3},
            "patients": [
                {
                    "patient_id": index + 1,
                    "validation_fold": fold,
                    "videos": [
                        {
                            "video_id": index + 1,
                            "center_id": 1,
                            "frame_ids": [],
                            "image_annotation_ids": [],
                            "segment_ids": [index + 1],
                        }
                    ],
                }
                for index, fold in enumerate([None, 0, 1])
            ],
        },
    )


def test_split_snapshot_is_canonical_and_immutable(snapshot: JsonObject) -> None:
    owner = User.objects.create_user(username="split-persistence-owner")
    dataset = AIDataSet.objects.create(name="Partition source")
    plan = AIDatasetSplitPlan.objects.create(
        created_by=owner, dataset=dataset, snapshot=snapshot
    )
    plan.refresh_from_db()
    assert plan.snapshot == SplitSnapshot.model_validate(snapshot).model_dump(
        mode="json"
    )
    with pytest.raises(ValidationError, match="immutable"):
        plan.save()
    assert AIDatasetSplitPlan.objects.count() == 1
    dataset.delete()
    plan.refresh_from_db()
    assert plan.dataset is None


@pytest.mark.parametrize("invalid", [{}, {"config": {}}, {"unexpected": True}])
def test_invalid_snapshot_rejects_direct_write(invalid: JsonObject) -> None:
    owner = User.objects.create_user(username="invalid-split-owner")
    with pytest.raises(ValidationError, match="Invalid split membership"):
        AIDatasetSplitPlan.objects.create(created_by=owner, snapshot=invalid)
    assert not AIDatasetSplitPlan.objects.exists()


def test_database_allows_only_one_active_transcode_per_video() -> None:
    from uuid import uuid4

    owner = User.objects.create_user(username="transcode-persistence-owner")
    center = Center.objects.create(name="Persistence center")
    video = VideoFile.objects.create(
        center=center, raw_video_hash="persistence-transcode"
    )
    job = VideoTranscodeJob.objects.create(
        actor=owner,
        video=video,
        idempotency_key=uuid4(),
        option="replace_processed",
        source_name="processed.mp4",
        source_sha256="a" * 64,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        VideoTranscodeJob.objects.create(
            actor=owner,
            video=video,
            idempotency_key=uuid4(),
            option="replace_processed",
            source_name="processed.mp4",
            source_sha256="a" * 64,
            status="running",
        )
    job.status = "completed"
    job.save(update_fields=["status"])
    VideoTranscodeJob.objects.create(
        actor=owner,
        video=video,
        idempotency_key=uuid4(),
        option="replace_processed",
        source_name="processed.mp4",
        source_sha256="a" * 64,
    )
