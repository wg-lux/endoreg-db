from __future__ import annotations

from typing import Any, cast
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import Group, User
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.db import connection
from django.test.utils import CaptureQueriesContext
from endoreg_db.helpers.typing import m2m_add_relation
from endoreg_db.models import (
    Center,
    Label,
    LabelVideoSegment,
    PortalUserInfo,
    SensitiveMeta,
    VideoFile,
    VideoState,
)
from tests.helpers.video_timeline import constant_rate_video_metadata
from endoreg_db.services.media.state import update_media_state
from tests.helpers.media_states import create_media_state


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
            raw_video_hash=f"video-media-{uuid4().hex}",
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
        assert payload["results"][0]["original_file_name"] == f"video-{self.video.pk}"

    def test_list_isolates_missing_processed_generation(self) -> None:
        self.client.force_login(self.user)
        VideoFile.objects.filter(pk=self.video.pk).update(
            processed_file="processed_videos_final/.generations/missing.mp4",
            processed_video_hash="a" * 64,
        )
        segment = LabelVideoSegment.objects.create(
            video_file=self.video, start_frame_number=0, end_frame_number=1
        )
        healthy = VideoFile.objects.create(
            center=self.center,
            raw_video_hash=f"healthy-{uuid4().hex}",
            meta=constant_rate_video_metadata(fps_num=25, frame_count=100),
        )
        LabelVideoSegment.objects.create(
            video_file=healthy, start_frame_number=0, end_frame_number=1
        )

        response = self.client.get("/api/media/videos/")

        assert response.status_code == 200, response.content
        rows = {row["id"]: row for row in response.json()["results"]}
        assert rows[self.video.pk]["status"] == "failed"
        assert rows[self.video.pk]["anonymized"] is False
        assert rows[self.video.pk]["segments"] is None
        assert "Canonical media is missing" in rows[self.video.pk]["timeline_error"]
        assert ".generations" not in rows[self.video.pk]["timeline_error"]
        assert rows[healthy.pk]["timeline_error"] is None
        assert rows[healthy.pk]["segments"][0]["start_time"] == 0.0
        segment.refresh_from_db()
        assert (segment.start_frame_number, segment.end_frame_number) == (0, 1)
        self.video.refresh_from_db()
        assert not (self.video.meta or {}).get("source_timeline")

    def test_list_tolerates_malformed_pagination(self) -> None:
        self.client.force_login(self.user)
        for value in ("", "undefined", "null", "NaN", "1.5", "-1", " ", "9" * 5000):
            with self.subTest(value=value[:20]):
                response = self.client.get(
                    "/api/media/videos/", {"limit": value, "offset": value}
                )
                assert response.status_code == 200, response.content
                assert response.json()["results"][0]["id"] == self.video.pk

    def test_list_normalizes_limit_and_preserves_pagination_filters(self) -> None:
        self.client.force_login(self.user)
        for limit, expected in (("0", "50"), ("500", "100"), ("2", "2")):
            with self.subTest(limit=limit):
                response = self.client.get(
                    "/api/media/videos/",
                    {"limit": limit, "offset": "1", "search": "video-media"},
                )
                assert response.status_code == 200, response.content
                payload = response.json()
                assert payload["count"] == 1
                assert payload["results"] == []
                assert payload["next"] is None
                assert f"limit={expected}" in payload["previous"]
                assert "offset=0" in payload["previous"]
                assert "search=video-media" in payload["previous"]

    def test_list_does_not_validate_unused_technical_metadata(self) -> None:
        self.client.force_login(self.user)
        VideoFile.objects.filter(pk=self.video.pk).update(fps=0, width=0, height=0)
        response = self.client.get("/api/media/videos/")
        assert response.status_code == 200, response.content
        assert response.json()["results"][0]["id"] == self.video.pk

    def test_list_internal_value_error_is_not_a_query_error(self) -> None:
        self.client.force_login(self.user)
        with patch(
            "endoreg_db.views.media.video_media.VideoFileListSerializer",
            side_effect=ValueError("internal metadata failure"),
        ):
            response = self.client.get("/api/media/videos/")
        assert response.status_code == 500
        assert response.json() == {"error": "Failed to retrieve video list"}

    def test_list_segments_reuse_one_video_and_metadata_without_extra_queries(
        self,
    ) -> None:
        self.client.force_login(self.user)
        VideoFile.objects.filter(pk=self.video.pk).update(
            fps=25.0,
            frame_count=100,
            duration=4.0,
            meta=constant_rate_video_metadata(fps_num=25, frame_count=100),
        )
        label = Label.objects.create(name=f"list-segments-{uuid4().hex}")
        original_from_db = VideoFile.from_db
        loaded: list[VideoFile] = []

        def record_video(
            db: str, field_names: list[str], values: list[object]
        ) -> VideoFile:
            video = original_from_db(db, field_names, values)
            loaded.append(video)
            return video

        query_counts: list[int] = []
        for segment_count in (1, 8):
            LabelVideoSegment.objects.filter(video_file=self.video).delete()
            LabelVideoSegment.objects.bulk_create(
                [
                    LabelVideoSegment(
                        video_file=self.video,
                        label=label,
                        start_frame_number=index * 2,
                        end_frame_number=index * 2 + 1,
                    )
                    for index in range(segment_count)
                ]
            )
            loaded.clear()
            with (
                self.subTest(segment_count=segment_count),
                patch.object(VideoFile, "from_db", side_effect=record_video),
                CaptureQueriesContext(connection) as queries,
            ):
                response = self.client.get("/api/media/videos/")
            assert response.status_code == 200, response.content
            query_counts.append(len(queries))
            assert len(loaded) == 1
            parent = loaded[0]
            with self.assertNumQueries(0):
                segments = list(parent.label_video_segments.all())
                assert len(segments) == segment_count
                assert all(segment.video_file is parent for segment in segments)
                assert all(
                    segment.video_file.meta is parent.meta for segment in segments
                )
            returned = response.json()["results"][0]["segments"]
            assert len(returned) == segment_count
            assert returned[-1]["end_time"] == (segment_count * 2 - 1) / 25
        assert query_counts[0] == query_counts[1]

    @override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
    def test_centerless_hub_list_only_returns_anonymized_processed_videos(self) -> None:
        incomplete = VideoFile.objects.create(
            center=self.center,
            raw_video_hash=f"incomplete-{uuid4().hex}",
            original_file_name="patient-name-incomplete.mp4",
        )
        processed = VideoFile.objects.create(
            center=self.center,
            raw_video_hash=f"processed-{uuid4().hex}",
            original_file_name="patient-name-processed.mp4",
            duration=12.5,
        )
        cast(Any, processed.processed_file).save(
            "processed-safe.mp4", ContentFile(b"processed"), save=True
        )
        state = processed.get_or_create_state()
        update_media_state(state, changes={"anonymized": True})
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
        update_media_state(state, changes={"anonymized": True})
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
        update_media_state(state, changes={"anonymized": True})
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

    def test_status_filter_follows_native_phase(self) -> None:
        def video_with(**flags: bool) -> int:
            return VideoFile.objects.create(
                center=self.center,
                raw_video_hash=f"phase-{uuid4().hex}",
                state=create_media_state(VideoState, was_created=False, **flags),
            ).pk

        expected = {
            "processing": {
                video_with(anonymized=True),
                video_with(processing_started=True),
            },
            "done_processing_anonymization": {
                video_with(sensitive_meta_processed=True)
            },
            "validated": {video_with(anonymization_validated=True)},
            "failed": {video_with(processing_error=True, anonymization_validated=True)},
        }
        expected["not_started"] = {self.video.pk, video_with()}
        self.client.force_login(self.user)

        for status_filter, video_ids in expected.items():
            response = self.client.get(
                "/api/media/videos/", {"status": status_filter, "limit": 50}
            )
            assert response.status_code == 200, response.content
            returned = {row["id"] for row in response.json()["results"]}
            assert returned == video_ids, status_filter
