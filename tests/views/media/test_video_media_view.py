from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

from django.contrib.auth.models import Group, User
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from endoreg_db.helpers.typing import m2m_add_relation
from endoreg_db.models import Center, PortalUserInfo, SensitiveMeta, VideoFile


class VideoMediaViewTests(TestCase):
    center: Center
    user: User
    video: VideoFile

    def setUp(self) -> None:
        suffix = uuid4().hex[:8]
        self.center = Center.objects.create(name=f"video-media-center-{suffix}")
        self.user = User.objects.create_user(username=f"video-media-user-{suffix}")
        video_read_group = Group.objects.create(name="video:read")
        m2m_add_relation(self.user.groups).add(video_read_group)  # type: ignore
        self.video = VideoFile.objects.create(
            center=self.center,
            video_hash=f"video-media-{uuid4().hex}",
            original_file_name="video-media.mp4",
        )
        portal_info = PortalUserInfo.objects.create(user=self.user)
        portal_info.centers.add(self.center)

    def test_list_videos_returns_results(self) -> None:
        self.client.force_login(self.user)

        response = self.client.get("/api/media/videos/")

        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["count"] == 1
        assert payload["results"][0]["id"] == self.video.pk
        assert payload["results"][0]["original_file_name"] == "video-media.mp4"

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_centerless_hub_list_only_returns_anonymized_processed_videos(self) -> None:
        incomplete = VideoFile.objects.create(
            center=self.center,
            video_hash=f"incomplete-{uuid4().hex}",
            original_file_name="patient-name-incomplete.mp4",
        )
        processed = VideoFile.objects.create(
            center=self.center,
            video_hash=f"processed-{uuid4().hex}",
            original_file_name="patient-name-processed.mp4",
            duration=12.5,
        )
        cast(Any, processed.processed_file).save(
            "processed-safe.mp4", ContentFile(b"processed"), save=True
        )
        state = processed.get_or_create_state()
        state.anonymized = True
        state.save(update_fields=["anonymized"])
        centerless = User.objects.create_user(username=f"centerless-{uuid4().hex}")
        m2m_add_relation(centerless.groups).add(Group.objects.get(name="video:read"))  # type: ignore
        self.client.force_login(centerless)

        response = self.client.get("/api/media/videos/")

        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["count"] == 1
        result = payload["results"][0]
        assert result["id"] == processed.pk
        assert "original_file_name" not in result
        assert "integrity_error" not in result
        assert "validated_annotators" not in result
        assert incomplete.pk != result["id"]

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_cross_center_hub_detail_omits_patient_and_storage_metadata(self) -> None:
        sensitive_meta = SensitiveMeta.objects.create(
            patient_first_name="Secret",
            patient_last_name="Patient",
            center=self.center,
        )
        self.video.sensitive_meta = sensitive_meta
        self.video.duration = 4.0
        self.video.save(update_fields=["sensitive_meta", "duration"])
        cast(Any, self.video.processed_file).save(
            "processed-secret.mp4", ContentFile(b"processed"), save=True
        )
        state = self.video.get_or_create_state()
        state.anonymized = True
        state.save(update_fields=["anonymized"])
        centerless = User.objects.create_user(username=f"hub-detail-{uuid4().hex}")
        m2m_add_relation(centerless.groups).add(Group.objects.get(name="video:read"))  # type: ignore
        self.client.force_login(centerless)

        response = self.client.get(f"/api/media/videos/{self.video.pk}/details/")

        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["id"] == self.video.pk
        assert payload["duration"] == 4.0
        for forbidden_field in (
            "file",
            "full_path",
            "patient_first_name",
            "patient_last_name",
            "patient_dob",
            "examination_date",
            "original_file_name",
            "integrity_error",
        ):
            assert forbidden_field not in payload

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_cross_center_hub_video_read_does_not_grant_patch(self) -> None:
        cast(Any, self.video.processed_file).save(
            "processed-write-boundary.mp4", ContentFile(b"processed"), save=True
        )
        state = self.video.get_or_create_state()
        state.anonymized = True
        state.save(update_fields=["anonymized"])
        centerless = User.objects.create_user(username=f"hub-writer-{uuid4().hex}")
        m2m_add_relation(centerless.groups).add(  # type: ignore
            Group.objects.create(name="video:write"),
            Group.objects.get(name="video:read"),
        )
        self.client.force_login(centerless)

        response = self.client.patch(
            f"/api/media/videos/{self.video.pk}/details/",
            {"export_segments_by_video": True},
            content_type="application/json",
        )

        assert response.status_code == 403
        self.video.refresh_from_db()
        assert self.video.export_segments_by_video is False
