from __future__ import annotations

# pyright: reportUnknownMemberType=false

import hashlib
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from django.core.files.base import ContentFile
from endoreg_db.models import (
    Center,
    Label,
    LabelVideoSegment,
    VideoFile,
    VideoProcessingHistory,
    VideoState,
)
from endoreg_db.models.state import video_segment_validation as segment_state
from endoreg_db.models.state.audit_ledger import AuditLedger


class VideoReadyExportEndpointTests(TestCase):
    def setUp(self):
        suffix = uuid4().hex[:8]
        self.center = Center.objects.create(name=f"ready-center-{suffix}")
        self.user = User.objects.create_user(
            username=f"ready-user-{suffix}",
            password="test-password",
            is_staff=True,
        )

    def _video(self, *, state: VideoState | None = None) -> VideoFile:
        content = b"processed-video-for-export"
        default_state = state is None
        video = VideoFile.objects.create(
            center=self.center,
            video_hash=f"ready-video-{uuid4().hex[:8]}",
            state=state
            or VideoState.objects.create(
                anonymization_validated=True,
                outside_segments_removed=True,
                segment_annotations_created=True,
                segment_annotations_validated=True,
            ),
        )
        video.processed_file.save(
            "processed.mp4",
            SimpleUploadedFile(
                "ready-processed.mp4",
                content,
                content_type="video/mp4",
            ),
            save=True,
        )
        video.save(update_fields=["processed_file"])
        self.processed_sha = hashlib.sha256(content).hexdigest()
        if default_state:
            video_state = video.state
            assert video_state is not None
            video_state.anonymization_validated = True
            video_state.outside_segments_removed = True
            video_state.segment_annotations_created = True
            video_state.segment_annotations_validated = True
            video_state.save(
                update_fields=[
                    "anonymization_validated",
                    "outside_segments_removed",
                    "segment_annotations_created",
                    "segment_annotations_validated",
                ]
            )
        return video

    def _mark_ready_state(self, video: VideoFile) -> None:
        state = video.get_or_create_state()
        state.anonymization_validated = True
        state.outside_segments_removed = True
        state.segment_annotations_created = True
        state.segment_annotations_validated = True
        state.ready_for_export = True
        state.ready_for_export_at = timezone.now()
        state.ready_for_export_by = self.user.username
        state.processed_file_sha256 = self.processed_sha
        state.save()

    def test_marks_existing_managed_video_ready(self):
        video = self._video()
        self.client.force_login(self.user)

        response = self.client.post(
            f"/api/media/videos/{video.pk}/mark-ready-for-export/",
            data={
                "center_key": self.center.center_key,
                "processed_file_sha256": self.processed_sha,
            },
            content_type="application/json",
        )

        assert response.status_code == 200, response.content
        body = response.json()
        assert body["success"] is True
        assert body["processed_file_sha256"] == self.processed_sha

        video.refresh_from_db()
        state = video.get_or_create_state()
        assert state.ready_for_export is True
        assert state.ready_for_export_by == self.user.username
        assert state.processed_file_sha256 == self.processed_sha
        assert state.ready_for_export_at is not None
        assert AuditLedger.objects.filter(
            object_type="VideoFile",
            object_pk=str(video.pk),
            action="ready_for_export",
        ).exists()

    def test_ledger_down_aborts_ready_promotion(self):
        video = self._video()
        self.client.force_login(self.user)

        with patch.object(AuditLedger, "save", return_value=None):
            response = self.client.post(
                f"/api/media/videos/{video.pk}/mark-ready-for-export/",
                data={
                    "center_key": self.center.center_key,
                    "processed_file_sha256": self.processed_sha,
                },
                content_type="application/json",
            )

        assert response.status_code == 503, response.content
        assert "Audit ledger unavailable" in response.json()["error"]
        video.refresh_from_db()
        state = video.get_or_create_state()
        assert state.ready_for_export is False
        assert state.ready_for_export_at is None
        assert state.ready_for_export_by == ""
        assert state.processed_file_sha256 == ""

    def test_rejects_when_outside_segments_are_not_removed(self):
        state = VideoState.objects.create(
            anonymization_validated=True,
            outside_segments_removed=False,
            segment_annotations_created=True,
            segment_annotations_validated=False,
        )
        video = self._video(state=state)
        self.client.force_login(self.user)

        response = self.client.post(
            f"/api/media/videos/{video.pk}/mark-ready-for-export/",
            data={"center_key": self.center.center_key},
            content_type="application/json",
        )

        assert response.status_code == 409, response.content
        video.refresh_from_db()
        assert video.get_or_create_state().ready_for_export is False

    def test_rejects_when_video_is_failed_lost(self):
        state = VideoState.objects.create(
            anonymization_validated=True,
            outside_segments_removed=True,
            segment_annotations_created=True,
            segment_annotations_validated=True,
            processing_error=True,
        )
        video = self._video(state=state)
        self.client.force_login(self.user)

        response = self.client.post(
            f"/api/media/videos/{video.pk}/mark-ready-for-export/",
            data={"center_key": self.center.center_key},
            content_type="application/json",
        )

        assert response.status_code == 409, response.content
        assert "failed/lost" in response.json()["error"]

    def test_rejects_when_segment_cleanup_is_not_final(self):
        for history_status, expected_status in (
            (None, "cleanup_required"),
            (VideoProcessingHistory.STATUS_PENDING, "cleanup_queued"),
            (VideoProcessingHistory.STATUS_RUNNING, "cleanup_running"),
            (VideoProcessingHistory.STATUS_FAILURE, "cleanup_failed"),
        ):
            with self.subTest(history_status=history_status):
                state = VideoState.objects.create(
                    anonymization_validated=True,
                    outside_segments_removed=True,
                    segment_annotations_created=True,
                    segment_annotations_validated=False,
                )
                video = self._video(state=state)
                state.refresh_from_db()
                state.outside_segments_removed = True
                state.segment_annotations_created = True
                state.segment_annotations_validated = False
                state.save(
                    update_fields=[
                        "outside_segments_removed",
                        "segment_annotations_created",
                        "segment_annotations_validated",
                        "date_modified",
                    ]
                )
                if history_status is not None:
                    VideoProcessingHistory.objects.create(
                        video=video,
                        operation=VideoProcessingHistory.OPERATION_REPROCESSING,
                        status=history_status,
                        task_id=f"cleanup-{history_status}",
                        config=segment_state.blackening_history_config(
                            only_validated=False
                        ),
                    )
                self.client.force_login(self.user)

                response = self.client.post(
                    f"/api/media/videos/{video.pk}/mark-ready-for-export/",
                    data={"center_key": self.center.center_key},
                    content_type="application/json",
                )

                assert response.status_code == 409, response.content
                assert expected_status in response.json()["error"]
                video.refresh_from_db()
                assert video.get_or_create_state().ready_for_export is False

    def test_rejects_processed_hash_mismatch(self):
        video = self._video()
        self.client.force_login(self.user)

        response = self.client.post(
            f"/api/media/videos/{video.pk}/mark-ready-for-export/",
            data={
                "center_key": self.center.center_key,
                "processed_file_sha256": "0" * 64,
            },
            content_type="application/json",
        )

        assert response.status_code == 409, response.content
        assert "processed_file_sha256" in response.json()["error"]

    def test_rejects_center_mismatch(self):
        other_center = Center.objects.create(name=f"other-ready-{uuid4().hex[:8]}")
        video = self._video()
        self.client.force_login(self.user)

        response = self.client.post(
            f"/api/media/videos/{video.pk}/mark-ready-for-export/",
            data={"center_key": other_center.center_key},
            content_type="application/json",
        )

        assert response.status_code == 403, response.content

    def test_processed_file_replacement_clears_export_readiness(self):
        video = self._video()
        self._mark_ready_state(video)

        video.processed_file.save(
            "replacement-processed.mp4",
            ContentFile(b"replacement-processed-video"),
            save=True,
        )

        video.refresh_from_db()
        state = video.get_or_create_state()
        assert state.ready_for_export is False
        assert state.outside_segments_removed is False
        assert state.processed_file_sha256 == ""

    def test_segment_creation_clears_export_readiness(self):
        video = self._video()
        self._mark_ready_state(video)
        label = Label.objects.create(name=f"outside-{uuid4().hex[:8]}")

        LabelVideoSegment.objects.create(
            video_file=video,
            label=label,
            start_frame_number=1,
            end_frame_number=5,
        )

        video.refresh_from_db()
        state = video.get_or_create_state()
        assert state.ready_for_export is False
        assert state.outside_segments_removed is False

    def test_segment_update_clears_export_readiness(self):
        video = self._video()
        label = Label.objects.create(name=f"outside-{uuid4().hex[:8]}")
        segment = LabelVideoSegment.objects.create(
            video_file=video,
            label=label,
            start_frame_number=1,
            end_frame_number=5,
        )
        self._mark_ready_state(video)

        segment.end_frame_number = 8
        segment.save(update_fields=["end_frame_number"])

        video.refresh_from_db()
        state = video.get_or_create_state()
        assert state.ready_for_export is False
        assert state.outside_segments_removed is False

    def test_segment_delete_clears_export_readiness(self):
        video = self._video()
        label = Label.objects.create(name=f"outside-{uuid4().hex[:8]}")
        segment = LabelVideoSegment.objects.create(
            video_file=video,
            label=label,
            start_frame_number=1,
            end_frame_number=5,
        )
        self._mark_ready_state(video)

        segment.delete()

        video.refresh_from_db()
        state = video.get_or_create_state()
        assert state.ready_for_export is False
        assert state.outside_segments_removed is False

    def test_outside_segment_validation_change_clears_export_readiness(self):
        video = self._video()
        label = Label.objects.create(name=f"outside-{uuid4().hex[:8]}")
        segment = LabelVideoSegment.objects.create(
            video_file=video,
            label=label,
            start_frame_number=1,
            end_frame_number=5,
        )
        segment_state, _ = segment.get_or_create_state()
        self._mark_ready_state(video)

        segment_state.is_validated = True
        segment_state.save(update_fields=["is_validated"])

        video.refresh_from_db()
        state = video.get_or_create_state()
        assert state.ready_for_export is False
        assert state.outside_segments_removed is False
