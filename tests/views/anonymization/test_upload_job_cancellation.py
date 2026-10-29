from typing import Protocol, cast

import pytest
from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import resolve, reverse
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate

from endoreg_db.models import Center, PortalUserInfo, UploadJob
from endoreg_db.services.hub.upload_job_import_lease import (
    acquire_upload_job_import_lease,
)
from endoreg_db.views.anonymization.overview import UploadJobCancelView

pytestmark = pytest.mark.django_db


class _Groups(Protocol):
    def add(self, *groups: Group) -> None: ...
    def clear(self) -> None: ...


class _GroupUser(Protocol):
    groups: _Groups


def test_cancel_route_is_mounted_on_the_canonical_endoreg_api() -> None:
    job_id = "00000000-0000-0000-0000-000000000001"
    route = reverse("api:anonymization_upload_job_cancel", kwargs={"job_id": job_id})

    assert route == f"/endoreg-api/anonymization/upload-jobs/{job_id}/cancel/"
    assert getattr(resolve(route).func, "view_class", None) is UploadJobCancelView


@pytest.fixture
def operator_job(monkeypatch: pytest.MonkeyPatch) -> tuple[User, UploadJob]:
    monkeypatch.setattr("endoreg_db.authz.permissions.is_debug_mode", lambda: False)
    center = Center.objects.create(name="Cancellation API center")
    user = User.objects.create_user(username="cancel-api-operator")
    cast(_GroupUser, user).groups.add(Group.objects.create(name="data:write"))
    PortalUserInfo.objects.create(user=user).centers.add(center)
    return user, UploadJob.objects.create(
        source_center=center,
        content_type="video/mp4",
        source_file_persisted=True,
        file=SimpleUploadedFile("cancel-api.mp4", b"retained source"),
    )


def cancel(job: UploadJob, user: User | None) -> Response:
    request = APIRequestFactory().post(f"/anonymization/upload-jobs/{job.pk}/cancel/")
    if user is not None:
        force_authenticate(request, user=user)
    response = UploadJobCancelView.as_view()(request, job_id=job.pk)
    assert isinstance(response, Response)
    return response


def test_cancel_reports_persisted_result_and_source_retention(
    operator_job: tuple[User, UploadJob],
) -> None:
    user, job = operator_job
    response = cancel(job, user)
    assert response.status_code == 200
    payload = cast(dict[str, object], response.data)
    assert payload["source_preserved"] is True
    assert "cancellation_requested" in payload
    result = cast(dict[str, object], payload["upload_job"])
    assert result["status"] == "cancelled"
    assert cancel(job, user).status_code == 200


def test_cancel_requires_authentication_write_permission_and_center_membership(
    operator_job: tuple[User, UploadJob],
) -> None:
    user, job = operator_job
    assert cancel(job, None).status_code in (401, 403)
    cast(_GroupUser, user).groups.clear()
    cast(_GroupUser, user).groups.add(Group.objects.create(name="data:read"))
    assert cancel(job, user).status_code == 403
    cast(_GroupUser, user).groups.add(Group.objects.get(name="data:write"))
    job.source_center = Center.objects.create(name="Other cancellation center")
    job.save()
    assert cancel(job, user).status_code == 403
    job.refresh_from_db()
    assert job.status == "pending"


def test_completed_import_returns_conflict(
    operator_job: tuple[User, UploadJob],
) -> None:
    user, job = operator_job
    UploadJob.objects.filter(pk=job.pk).update(status="anonymized")
    assert cancel(job, user).status_code == 409


def test_active_execution_reports_accepted_intent(
    operator_job: tuple[User, UploadJob],
) -> None:
    user, job = operator_job
    lease = acquire_upload_job_import_lease(
        upload_job_id=str(job.pk), owner="execution-api"
    )
    response = cancel(job, user)
    assert response.status_code == 202
    payload = cast(dict[str, object], response.data)
    assert payload["cancellation_requested"] is True
    assert payload["source_preserved"] is True
    result = cast(dict[str, object], payload["upload_job"])
    assert result["status"] == "cancel_requested"
    job.refresh_from_db()
    assert job.processing_lease_owner == lease.owner


def test_cancel_rejects_client_supplied_actor(
    operator_job: tuple[User, UploadJob],
) -> None:
    user, job = operator_job
    request = APIRequestFactory().post(
        f"/anonymization/upload-jobs/{job.pk}/cancel/",
        {"actor_id": user.pk + 1},
        format="json",
    )
    force_authenticate(request, user=user)
    response = UploadJobCancelView.as_view()(request, job_id=job.pk)
    assert response.status_code == 400
    job.refresh_from_db()
    assert job.status == "pending"
