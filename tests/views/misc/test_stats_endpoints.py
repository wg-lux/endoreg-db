from __future__ import annotations

from datetime import date, datetime, time
from uuid import uuid4

from django.test import TestCase

from endoreg_db.models import (
    Center,
    Examination,
    Label,
    LabelVideoSegment,
    Patient,
    PatientExamination,
    SensitiveMeta,
    VideoFile,
)


class StatsEndpointTests(TestCase):
    def setUp(self):
        suffix = uuid4().hex[:8]
        self.center = Center.objects.create(name=f"stats-center-{suffix}")
        self.patient = Patient.objects.create(
            first_name="Stats",
            last_name="Patient",
            center=self.center,
            patient_hash=f"stats-patient-{uuid4().hex}",
        )
        self.examination = Examination.objects.create(name=f"stats-exam-{suffix}")
        self.patient_examination = PatientExamination.objects.create(
            patient=self.patient,
            examination=self.examination,
            date_start=date(2026, 1, 1),
            hash=f"stats-pe-{uuid4().hex}",
        )
        self.sensitive_meta = SensitiveMeta.objects.create(
            patient_first_name="Stats",
            patient_last_name="Patient",
            patient_dob=datetime(1990, 1, 1, 0, 0),
            examination_date=date(2026, 1, 1),
            examination_time=time(9, 0),
            center=self.center,
            pseudo_patient=self.patient,
            pseudo_examination=self.patient_examination,
        )
        self.video = VideoFile.objects.create(
            center=self.center,
            patient=self.patient,
            examination=self.patient_examination,
            sensitive_meta=self.sensitive_meta,
            video_hash=f"stats-video-{uuid4().hex}",
            original_file_name="stats.mp4",
        )
        label = Label.objects.create(name=f"stats-label-{suffix}")
        LabelVideoSegment.objects.create(
            video_file=self.video,
            label=label,
            start_frame_number=1,
            end_frame_number=2,
        )

    def test_examination_stats_endpoint(self):
        response = self.client.get("/api/examinations/stats/")
        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["status"] == "success"
        assert "total_examinations" in payload
        assert "total_patient_examinations" in payload
        assert "recent_examinations" in payload

    def test_video_segment_stats_endpoints(self):
        for path in ["/api/video-segment/stats/", "/api/video-segments/stats/"]:
            response = self.client.get(path)
            assert response.status_code == 200, response.content
            payload = response.json()
            assert payload["status"] == "success"
            assert "total_segments" in payload
            assert "total_videos" in payload
            assert "label_distribution" in payload

    def test_sensitive_meta_stats_endpoint(self):
        response = self.client.get("/api/video/sensitivemeta/stats/")
        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["status"] == "success"
        assert "total_sensitive_meta" in payload
        assert "verified_meta" in payload

    def test_general_stats_endpoint(self):
        response = self.client.get("/api/stats/")
        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["status"] == "success"
        assert "overview" in payload
        assert "system_status" in payload
