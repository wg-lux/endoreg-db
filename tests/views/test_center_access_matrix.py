from __future__ import annotations

# pyright: reportUnknownMemberType=false

from unittest.mock import patch
from typing import cast

import pytest
from django.contrib.auth.models import Group, User
from django.test import Client, override_settings

from endoreg_db.models import Center, Patient, PortalUserInfo, VideoFile

pytestmark = pytest.mark.django_db

DEPLOYMENT_ROLES = (
    "standalone",
    "site_node",
    "local_study_server",
    "central_hub",
)


def _user_with_centers(username: str, role: str, *centers: Center) -> User:
    user = User.objects.create_user(username=username)
    user.groups.add(Group.objects.get_or_create(name=role)[0])
    portal_info = PortalUserInfo.objects.create(user=user)
    portal_info.centers.set(centers)
    return user


@pytest.mark.parametrize("deployment_role", DEPLOYMENT_ROLES)
def test_patient_list_remains_center_scoped_in_every_deployment(
    client: Client, deployment_role: str
) -> None:
    own_center = Center.objects.create(name=f"own-{deployment_role}")
    foreign_center = Center.objects.create(name=f"foreign-{deployment_role}")
    own_patient = Patient.objects.create(
        first_name="Own", last_name="Patient", center=own_center
    )
    foreign_patient = Patient.objects.create(
        first_name="Foreign", last_name="Patient", center=foreign_center
    )
    user = _user_with_centers(
        f"patient-reader-{deployment_role}", "patient:read", own_center
    )
    client.force_login(user)

    with (
        override_settings(ENDOREG_DEPLOYMENT_ROLE=deployment_role),
        patch("endoreg_db.authz.permissions.is_debug_mode", return_value=False),
    ):
        response = client.get("/api/patients/")

    assert response.status_code == 200, response.content
    response_rows = cast(list[dict[str, object]], response.json())
    patient_ids = {int(cast(int, item["id"])) for item in response_rows}
    assert own_patient.pk in patient_ids
    assert foreign_patient.pk not in patient_ids


@override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
def test_hub_video_reader_does_not_gain_upload_export_admin_or_write_access(
    client: Client,
) -> None:
    center = Center.objects.create(name="hub-video-reader-center")
    video = VideoFile.objects.create(center=center, video_hash="hub-write-denial")
    user = _user_with_centers("hub-video-reader", "video:read", center)
    client.force_login(user)

    requests: tuple[tuple[str, str, dict[str, object]], ...] = (
        ("post", "/api/upload/", {}),
        ("post", "/api/media/videos/export-annotated/", {}),
        (
            "post",
            f"/api/media/videos/{video.pk}/mark-ready-for-export/",
            {},
        ),
        ("post", f"/api/media/videos/{video.pk}/segments/bulk/", {}),
    )
    with patch("endoreg_db.authz.permissions.is_debug_mode", return_value=False):
        for method, path, payload in requests:
            response = getattr(client, method)(
                path, data=payload, content_type="application/json"
            )
            assert response.status_code == 403, (path, response.content)

    admin_response = client.get("/admin/")
    assert admin_response.status_code != 200


@override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
def test_video_writer_cannot_mutate_foreign_center_video(client: Client) -> None:
    own_center = Center.objects.create(name="writer-own-center")
    foreign_center = Center.objects.create(name="writer-foreign-center")
    foreign_video = VideoFile.objects.create(
        center=foreign_center,
        video_hash="foreign-center-write-denial",
    )
    user = _user_with_centers("hub-video-writer", "video:write", own_center)
    client.force_login(user)

    with patch("endoreg_db.authz.permissions.is_debug_mode", return_value=False):
        response = client.post(
            f"/api/media/videos/{foreign_video.pk}/segments/bulk/",
            data={},
            content_type="application/json",
        )

    assert response.status_code == 403, response.content
