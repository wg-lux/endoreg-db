from datetime import timedelta
import json
from pathlib import Path
from typing import Protocol, cast
from uuid import uuid4

import pytest
from django.contrib.auth.models import Group, User
from django.db import models
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.response import Response

from endoreg_db.models import Center, PortalUserInfo, UploadJob, VideoFile
from endoreg_db.services.hub.import_monitoring import dismissed_upload_job_filter
from endoreg_db.utils.encryption.encrypted import EncryptedStorage
from endoreg_db.views.anonymization.overview import (
    AnonymizationOverviewView,
    UploadJobDismissView,
)

pytestmark = pytest.mark.django_db


class _Groups(Protocol):
    def add(self, *groups: Group) -> None: ...
    def clear(self) -> None: ...


class _GroupUser(Protocol):
    groups: _Groups


@pytest.fixture
def operator(monkeypatch: pytest.MonkeyPatch) -> tuple[User, Center]:
    monkeypatch.setattr("endoreg_db.authz.permissions.is_debug_mode", lambda: False)
    monkeypatch.setattr(
        cast(models.FileField, UploadJob._meta.get_field("file")),
        "storage",
        EncryptedStorage(),
    )
    center = Center.objects.create(name="Dismissal center")
    user = User.objects.create_user(username="dismiss-operator")
    cast(_GroupUser, user).groups.add(Group.objects.create(name="data:write"))
    PortalUserInfo.objects.create(user=user).centers.add(center)
    return user, center


def dismiss(job: UploadJob, user: User | None) -> Response:
    request = APIRequestFactory().post(f"/anonymization/upload-jobs/{job.pk}/dismiss/")
    if user is not None:
        force_authenticate(request, user=user)
    return UploadJobDismissView.as_view()(request, job_id=job.pk)


@pytest.mark.parametrize(
    "status,error_code",
    [
        ("error", "processing_failed"),
        ("lost", "source_missing"),
        ("error", "duplicate_content"),
    ],
)
def test_dismissal_preserves_source_history_and_media_and_is_idempotent(
    operator: tuple[User, Center], status: str, error_code: str
) -> None:
    user, center = operator
    job = UploadJob.objects.create(
        source_center=center,
        status=status,
        error_code=error_code,
        content_hash="source-identity",
        file=SimpleUploadedFile("retained.mp4", b"retained source"),
        error_detail="original technical evidence",
    )
    video = VideoFile.objects.create(center=center, video_hash="source-identity")
    ciphertext = Path(job.file.path).read_bytes()
    assert ciphertext.startswith(b"LXENC01")
    updated_at = job.updated_at
    assert dismiss(job, user).status_code == 204
    job.refresh_from_db()
    first_dismissal = job.overview_dismissed_at
    assert first_dismissal is not None
    assert getattr(job, "overview_dismissed_by_id") == user.pk
    assert dismiss(job, user).status_code == 204
    job.refresh_from_db()
    assert job.overview_dismissed_at == first_dismissal
    assert job.updated_at == updated_at
    assert job.status == status and job.error_detail == "original technical evidence"
    assert Path(job.file.path).read_bytes() == ciphertext
    assert VideoFile.objects.filter(pk=video.pk).exists()
    assert (
        not UploadJob.objects.exclude(dismissed_upload_job_filter())
        .filter(pk=job.pk)
        .exists()
    )


def test_dismissed_import_only_row_disappears_and_new_attempt_failure_reappears(
    operator: tuple[User, Center],
) -> None:
    user, center = operator
    job = UploadJob.objects.create(
        source_center=center, status="error", error_code="invalid_input"
    )

    def rows() -> list[dict[str, object]]:
        request = APIRequestFactory().get("/anonymization/items/overview/")
        force_authenticate(request, user=user)
        response = AnonymizationOverviewView.as_view(permission_classes=[])(request)
        return cast(list[dict[str, object]], json.loads(response.content))

    assert rows()[0]["can_dismiss_import"] is True
    assert dismiss(job, user).status_code == 204
    assert rows() == []
    job.refresh_from_db()
    job.mark_processing()
    assert len(rows()) == 1
    job.status = "error"
    job.error_code = "processing_failed"
    job.save()
    assert len(rows()) == 1
    assert dismiss(job, user).status_code == 204
    assert rows() == []


@pytest.mark.parametrize(
    "state", ["pending", "processing", "anonymized", "leased", "cleanup"]
)
def test_active_or_successful_jobs_cannot_be_dismissed(
    operator: tuple[User, Center], state: str
) -> None:
    user, center = operator
    fields: dict[str, object] = {"source_center": center}
    if state in {"leased", "cleanup"}:
        fields.update(status="error", error_code="processing_failed")
    else:
        fields["status"] = state
    if state == "leased":
        fields.update(
            processing_lease_owner="worker",
            processing_heartbeat_at=timezone.now(),
            processing_lease_expires_at=timezone.now() + timedelta(minutes=1),
        )
    if state == "cleanup":
        fields.update(
            cleanup_status="deleting",
            cleanup_receipt_id=uuid4(),
            cleanup_started_at=timezone.now(),
            cleanup_fencing_token=1,
            cleanup_source_content_sha256="a" * 64,
            cleanup_source_name_sha256="b" * 64,
            cleanup_source_size_bytes=1,
        )
    job = UploadJob.objects.create(**fields)
    assert dismiss(job, user).status_code == 409
    job.refresh_from_db()
    assert job.overview_dismissed_at is None


def test_dismissal_requires_authentication_write_permission_and_center_membership(
    operator: tuple[User, Center],
) -> None:
    user, center = operator
    job = UploadJob.objects.create(
        source_center=center, status="lost", error_code="source_missing"
    )
    assert dismiss(job, None).status_code in (401, 403)
    cast(_GroupUser, user).groups.clear()
    cast(_GroupUser, user).groups.add(Group.objects.create(name="data:read"))
    assert dismiss(job, user).status_code == 403
    cast(_GroupUser, user).groups.add(Group.objects.get(name="data:write"))
    other = Center.objects.create(name="Foreign center")
    job.source_center = other
    job.save()
    assert dismiss(job, user).status_code == 403
    job.refresh_from_db()
    assert job.overview_dismissed_at is None
