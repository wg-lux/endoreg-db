from __future__ import annotations

import re
from contextlib import contextmanager
from datetime import date, timedelta
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.utils import timezone

from endoreg_db.models import (
    AnonymizationFieldMetric,
    AnonymizationMetricField,
    AnonymizationValidationMetric,
    Center,
    Frame,
    FrameBoxAnnotation,
    InformationSource,
    Label,
    RawPdfFile,
    SensitiveMeta,
    VideoFile,
)


class AnonymizationMetricsEndpointTests(TestCase):
    def setUp(self):
        suffix = uuid4().hex[:8]
        self.user = User.objects.create_user(username=f"metrics-user-{suffix}")
        self.anonymization_read_role = Group.objects.create(name="anonymization:read")
        self.video_read_role = Group.objects.create(name="video:read")
        self.center = Center.objects.create(name=f"metrics-center-{suffix}")
        self.other_center = Center.objects.create(name=f"other-metrics-center-{suffix}")
        self.sensitive_meta = SensitiveMeta.objects.create(
            center=self.center,
            patient_first_name="Endpoint",
            patient_last_name="Metric",
            examination_date=date(2026, 1, 1),
        )
        self.video = VideoFile.objects.create(
            center=self.center,
            sensitive_meta=self.sensitive_meta,
            video_hash=f"metrics-video-{uuid4().hex}",
            original_file_name="metrics.mp4",
        )
        self.pdf = RawPdfFile.objects.create(
            center=self.center,
            sensitive_meta=self.sensitive_meta,
            pdf_hash=f"metrics-pdf-{uuid4().hex}",
            raw_meta={"document_type": "report_final"},
        )
        today = timezone.localdate()
        self.metrics_params = {
            "date_from": (today - timedelta(days=1)).isoformat(),
            "date_to": (today + timedelta(days=1)).isoformat(),
        }

    def test_metrics_endpoint_denies_anonymous_users_in_production_mode(self):
        with self._production_permissions():
            response = self.client.get("/api/media/anonymization/metrics/")

        assert response.status_code in {401, 403}

    def test_metrics_endpoint_denies_authenticated_users_without_anonymization_role(
        self,
    ):
        user = User.objects.create_user(username=f"metrics-video-role-{uuid4().hex}")
        user.groups.add(self.video_read_role)
        self.client.force_login(user)

        with self._production_permissions():
            response = self.client.get(
                "/api/media/anonymization/metrics/",
                self.metrics_params,
            )

        assert response.status_code in {401, 403}

    def test_metrics_endpoint_allows_anonymization_read_role_in_production_mode(self):
        user = User.objects.create_user(username=f"metrics-reader-{uuid4().hex}")
        user.groups.add(self.anonymization_read_role)
        self.client.force_login(user)

        with self._production_permissions():
            response = self.client.get(
                "/api/media/anonymization/metrics/",
                self.metrics_params,
            )

        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["schema_version"] == "1.0"
        self._assert_snake_case_keys(payload)

    def test_empty_metrics_endpoint_returns_stable_snake_case_payload(self):
        response = self.client.get(
            "/api/media/anonymization/metrics/",
            self.metrics_params,
        )
        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["schema_version"] == "1.0"
        assert payload["query_bounds"]["max_window_days"] == 31
        assert "workflow" in payload
        assert "field_quality" in payload
        assert "phi_regions" in payload
        assert payload["workflow"]["validation_event_count"] == 0
        assert payload["workflow"]["median_seconds_to_validation"] is None
        self._assert_snake_case_keys(payload)

    def test_metrics_endpoint_requires_date_range(self):
        response = self.client.get("/api/media/anonymization/metrics/")

        assert response.status_code == 400, response.content
        assert "date_from and date_to are required" in response.json()["error"]

    def test_metrics_endpoint_rejects_overwide_date_range(self):
        response = self.client.get(
            "/api/media/anonymization/metrics/",
            {
                "date_from": "2026-01-01",
                "date_to": "2026-03-01",
            },
        )

        assert response.status_code == 400, response.content
        assert "span no more than 31 days" in response.json()["error"]

    def test_metrics_endpoint_filters_field_quality_by_document_type_and_center(self):
        matching_metric = self._create_validation_metric(
            media_type="pdf",
            pdf=self.pdf,
            document_type="report_final",
            center=self.center,
        )
        self._create_field_metric(
            validation_metric=matching_metric,
            field_name=AnonymizationMetricField.DOCUMENT_TYPE,
            changed=False,
            exact_match=True,
        )
        other_pdf = RawPdfFile.objects.create(
            center=self.other_center,
            pdf_hash=f"other-metrics-pdf-{uuid4().hex}",
            raw_meta={"document_type": "pathology_final"},
        )
        other_metric = self._create_validation_metric(
            media_type="pdf",
            pdf=other_pdf,
            document_type="pathology_final",
            center=self.other_center,
        )
        self._create_field_metric(
            validation_metric=other_metric,
            field_name=AnonymizationMetricField.DOCUMENT_TYPE,
            changed=True,
            exact_match=False,
        )

        response = self.client.get(
            "/api/media/anonymization/metrics/",
            {
                **self.metrics_params,
                "media_type": "pdf",
                "document_type": "report_final",
                "center_id": str(self.center.pk),
            },
        )

        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["workflow"]["validation_event_count"] == 1
        document_metric = next(
            row
            for row in payload["field_quality"]
            if row["field_name"] == AnonymizationMetricField.DOCUMENT_TYPE
        )
        assert document_metric["support"] == 1
        assert document_metric["exact_match_rate"] == 1.0
        assert document_metric["changed_rate"] == 0.0
        self._assert_snake_case_keys(payload)

    def test_phi_region_metrics_count_proposals_and_human_matches(self):
        frame = Frame.objects.create(
            video=self.video,
            frame_number=1,
            relative_path="frame_0000001.jpg",
        )
        label = Label.objects.create(name="phi_region")
        proposal_source = InformationSource.objects.create(
            name="lx_anonymizer_phi_detector"
        )
        human_source = InformationSource.objects.create(name="human_validation")
        FrameBoxAnnotation.objects.create(
            frame=frame,
            label=label,
            x=10,
            y=10,
            width=100,
            height=50,
            image_width=800,
            image_height=600,
            information_source=proposal_source,
            annotator="system:lx_anonymizer",
            float_value=0.9,
        )
        FrameBoxAnnotation.objects.create(
            frame=frame,
            label=label,
            x=12,
            y=12,
            width=96,
            height=46,
            image_width=800,
            image_height=600,
            information_source=human_source,
            annotator="human-validator",
        )

        response = self.client.get(
            "/api/media/anonymization/metrics/",
            self.metrics_params,
        )

        assert response.status_code == 200, response.content
        phi_regions = response.json()["phi_regions"]
        assert phi_regions["proposal_count"] == 1
        assert phi_regions["human_annotation_count"] == 1
        assert phi_regions["matched_count"] == 1
        assert phi_regions["precision"] == 1.0
        assert phi_regions["recall"] == 1.0
        assert phi_regions["matching_evaluated"] is True

    def test_phi_region_matching_is_skipped_when_annotation_limit_is_exceeded(self):
        frame = Frame.objects.create(
            video=self.video,
            frame_number=2,
            relative_path="frame_0000002.jpg",
        )
        label = Label.objects.create(name="phi_region")
        proposal_source = InformationSource.objects.create(
            name="lx_anonymizer_phi_detector"
        )
        human_source = InformationSource.objects.create(name="human_validation")
        FrameBoxAnnotation.objects.create(
            frame=frame,
            label=label,
            x=10,
            y=10,
            width=100,
            height=50,
            image_width=800,
            image_height=600,
            information_source=proposal_source,
            annotator="system:lx_anonymizer",
        )
        FrameBoxAnnotation.objects.create(
            frame=frame,
            label=label,
            x=12,
            y=12,
            width=96,
            height=46,
            image_width=800,
            image_height=600,
            information_source=human_source,
            annotator="human-validator",
        )

        with patch(
            "endoreg_db.services.anonymization_metrics."
            "MAX_PHI_REGION_MATCH_ANNOTATIONS",
            1,
        ):
            response = self.client.get(
                "/api/media/anonymization/metrics/",
                self.metrics_params,
            )

        assert response.status_code == 200, response.content
        phi_regions = response.json()["phi_regions"]
        assert phi_regions["proposal_count"] == 1
        assert phi_regions["human_annotation_count"] == 1
        assert phi_regions["matching_annotation_count"] == 2
        assert phi_regions["matching_evaluated"] is False
        assert phi_regions["matched_count"] is None
        assert phi_regions["precision"] is None
        assert phi_regions["recall"] is None

    def test_metrics_endpoint_does_not_expose_patient_values_or_paths(self):
        metric = self._create_validation_metric(
            media_type="video",
            video=self.video,
            center=self.center,
        )
        self._create_field_metric(
            validation_metric=metric,
            field_name=AnonymizationMetricField.PATIENT_LAST_NAME,
            changed=True,
            exact_match=False,
        )

        response = self.client.get(
            "/api/media/anonymization/metrics/",
            self.metrics_params,
        )

        assert response.status_code == 200, response.content
        payload_text = response.content.decode()
        assert "Endpoint" not in payload_text
        assert "Metric" not in payload_text
        assert "/tmp/" not in payload_text
        assert "raw_file" not in payload_text
        assert "file_path" not in payload_text

    def _create_validation_metric(
        self,
        *,
        media_type: str,
        center: Center,
        video: VideoFile | None = None,
        pdf: RawPdfFile | None = None,
        document_type: str = "",
    ) -> AnonymizationValidationMetric:
        return AnonymizationValidationMetric.objects.create(
            media_type=media_type,
            video=video,
            pdf=pdf,
            sensitive_meta=self.sensitive_meta,
            center=center,
            validator_user=self.user,
            validator_username=self.user.username,
            validated_at=timezone.now(),
            status_before="done_processing_anonymization",
            status_after="validated",
            document_type=document_type,
            source_system="api",
            anonymizer_version="test-version",
            total_fields=1,
            changed_fields=0,
            exact_match_fields=1,
            missing_after_validation_fields=0,
            mean_similarity=1.0,
        )

    @staticmethod
    def _create_field_metric(
        *,
        validation_metric: AnonymizationValidationMetric,
        field_name: str,
        changed: bool,
        exact_match: bool,
    ) -> AnonymizationFieldMetric:
        return AnonymizationFieldMetric.objects.create(
            validation_metric=validation_metric,
            field_name=field_name,
            present_before=True,
            present_after=True,
            changed=changed,
            exact_match=exact_match,
            similarity_score=1.0 if exact_match else 0.5,
            was_required=True,
            was_empty_after_validation=False,
        )

    def _assert_snake_case_keys(self, value):
        snake_case = re.compile(r"^[a-z][a-z0-9_]*$")
        if isinstance(value, dict):
            for key, child in value.items():
                assert snake_case.match(key), key
                self._assert_snake_case_keys(child)
        elif isinstance(value, list):
            for child in value:
                self._assert_snake_case_keys(child)

    @contextmanager
    def _production_permissions(self):
        with (
            patch("endoreg_db.utils.permissions.is_debug_mode", return_value=False),
            patch("endoreg_db.authz.permissions.is_debug_mode", return_value=False),
        ):
            yield
