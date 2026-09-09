from __future__ import annotations

from uuid import uuid4

from django.test import TestCase

from endoreg_db.models import Center, VideoFile, VideoState
from endoreg_db.models.state.anonymization import AnonymizationState


class VideoStateAnonymizationStatusTests(TestCase):
    def test_sql_status_case_matches_python_property_for_dashboard_states(self):
        state_flags = [
            {"processing_error": True, "anonymization_validated": True},
            {"anonymization_validated": True},
            {"sensitive_meta_processed": True},
            {"frames_extracted": True, "anonymized": False},
            {"was_created": True, "frames_extracted": False},
            {"was_created": False, "processing_started": True},
            {"was_created": False, "anonymized": True},
            {"was_created": False},
        ]

        for flags in state_flags:
            state = VideoState.objects.create(**flags)
            sql_status = (
                VideoState.objects.filter(pk=state.pk)
                .annotate(status_value=VideoState.anonymization_status_case())
                .values_list("status_value", flat=True)
                .get()
            )

            assert sql_status == state.anonymization_status.value

    def test_sql_status_case_treats_missing_video_state_as_not_started(self):
        center = Center.objects.create(name=f"status-center-{uuid4().hex[:8]}")
        video = VideoFile.objects.create(
            center=center,
            video_hash=f"status-video-{uuid4().hex}",
        )

        sql_status = (
            VideoFile.objects.filter(pk=video.pk)
            .annotate(
                status_value=VideoState.anonymization_status_case(
                    relation_prefix="state",
                    include_missing_relation=True,
                )
            )
            .values_list("status_value", flat=True)
            .get()
        )

        assert sql_status == AnonymizationState.NOT_STARTED.value
