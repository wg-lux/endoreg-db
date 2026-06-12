from __future__ import annotations

from datetime import date, datetime, time
from unittest.mock import patch
from uuid import uuid4

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

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
from endoreg_db.models.state.audit_ledger import AuditLedger
from endoreg_db.services.audit_integrity import (
    AUDIT_LEDGER_INTEGRITY_CACHE_KEY,
    AUDIT_LEDGER_INTEGRITY_LOCK_KEY,
    refresh_audit_ledger_integrity_status,
    refresh_audit_ledger_integrity_status_once,
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
            patient_dob=timezone.make_aware(datetime(1990, 1, 1, 0, 0)),
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
        assert "audit_ledger_integrity" in payload["system_status"]

    def test_audit_ledger_integrity_endpoint_returns_cached_status(self):
        cache.set(
            AUDIT_LEDGER_INTEGRITY_CACHE_KEY,
            {
                "status": "verified",
                "verified": True,
                "checked_at": "2026-01-01T00:00:00+00:00",
                "entry_count": 1,
                "error": None,
                "ledger_head_hash": "a" * 64,
                "last_entry_id": "entry-1",
            },
            timeout=None,
        )

        with patch(
            "endoreg_db.models.state.audit_ledger.AuditLedger.verify_chain",
            side_effect=AssertionError("request path must not verify full chain"),
        ):
            response = self.client.get("/api/audit-ledger/integrity/")

        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["status"] == "verified"
        assert payload["verified"] is True
        assert payload["source"] == "cache"

    def test_audit_ledger_integrity_refresh_detects_tampering(self):
        first = AuditLedger.append_identity_commit(
            object_type="SensitiveMeta",
            object_pk="stats-1",
            data={"payload_hash": "first", "examination_hash": "exam-1"},
        )
        assert first is not None

        clean_status = refresh_audit_ledger_integrity_status()
        assert clean_status["status"] == "verified"
        assert clean_status["verified"] is True

        AuditLedger.objects.filter(pk=first.pk).update(
            data={"payload_hash": "tampered", "examination_hash": "exam-1"}
        )

        tampered_status = refresh_audit_ledger_integrity_status()
        assert tampered_status["status"] == "failed"
        assert tampered_status["verified"] is False

        response = self.client.get("/api/audit-ledger/integrity/")
        assert response.status_code == 200, response.content
        assert response.json()["status"] == "failed"

    def test_audit_ledger_integrity_refresh_skips_when_locked(self):
        cache.set(
            AUDIT_LEDGER_INTEGRITY_CACHE_KEY,
            {
                "status": "verified",
                "verified": True,
                "checked_at": "2026-01-01T00:00:00+00:00",
                "entry_count": 1,
                "error": None,
                "ledger_head_hash": "b" * 64,
                "last_entry_id": "entry-2",
            },
            timeout=None,
        )
        cache.set(AUDIT_LEDGER_INTEGRITY_LOCK_KEY, "locked", timeout=60)

        with patch(
            "endoreg_db.models.state.audit_ledger.AuditLedger.verify_chain",
            side_effect=AssertionError("locked refresh must not verify full chain"),
        ):
            payload = refresh_audit_ledger_integrity_status_once()

        assert payload["status"] == "verified"
        assert payload["source"] == "skipped_locked"
