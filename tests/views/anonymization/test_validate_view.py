"""
Comprehensive unit tests for AnonymizationValidateView.

Tests cover:
- Video file validation endpoint
- report file validation endpoint
- German and ISO date format support
- Error handling and edge cases
- Center name handling
"""

import logging
from datetime import date, datetime
from typing import Dict, cast
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.utils import timezone
from django.utils.translation import override
from rest_framework import status
from rest_framework.response import Response as DRFResponse
from rest_framework.test import APIRequestFactory, force_authenticate

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.media.anonymization_metrics import (
    AnonymizationFieldMetric,
    AnonymizationMetricField,
    AnonymizationValidationMetric,
)
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.models.other.tag import Tag
from endoreg_db.views.anonymization.validate import AnonymizationValidateView

logger = logging.getLogger(__name__)


@pytest.mark.django_db
class TestAnonymizationValidateView:
    """Test suite for AnonymizationValidateView."""

    # ------------------------------------------------------------------ #
    # Fixtures                                                           #
    # ------------------------------------------------------------------ #

    @pytest.fixture
    def factory(self) -> APIRequestFactory:
        """Create APIRequestFactory."""
        return APIRequestFactory()

    @pytest.fixture
    def user(self) -> User:
        """Create test user."""
        return User.objects.create_user(username="testuser")

    @pytest.fixture
    def center(self) -> Center:
        """Create test center."""
        return Center.objects.create(name="Test Center")

    @pytest.fixture
    def video_file(self, center: Center) -> VideoFile:
        """Create a minimal VideoFile instance for tests."""
        # Adjust fields here if your VideoFile model requires more non-null fields.
        return VideoFile.objects.create(center=center)

    @pytest.fixture
    def pdf_file(self, center: Center) -> RawPdfFile:
        """Create a minimal RawPdfFile instance for tests."""
        # Adjust fields here if your RawPdfFile model requires more non-null fields.
        return RawPdfFile.objects.create(center=center)

    # ------------------------------------------------------------------ #
    # Helper methods                                                     #
    # ------------------------------------------------------------------ #

    def _call_view(self, view, request, **kwargs) -> DRFResponse:
        """Call a DRF view and return the Response."""
        response = view(request, **kwargs)
        assert isinstance(response, DRFResponse)
        return response

    def _response_data(self, response: DRFResponse) -> Dict:
        """Return response.data as a dict."""
        assert hasattr(response, "data")
        assert isinstance(response.data, dict)
        return cast(Dict, response.data)

    def _payload_text(self, payload: Dict, key: str) -> str:
        """Extract a text value from payload, flattening lists if necessary."""
        value = payload.get(key, "")
        if isinstance(value, list):
            return " ".join(str(v) for v in value)
        return str(value)

    # ------------------------------------------------------------------ #
    # Tests                                                              #
    # ------------------------------------------------------------------ #

    def test_validate_video_success(self, factory, user, video_file):
        """Test successful video validation with ISO dates."""
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "1994-03-21",  # ISO format
            "examination_date": "2024-02-15",  # ISO format
            "casenumber": "12345",
            "is_verified": True,
            "file_type": "video",
        }

        with patch.object(VideoFile, "validate_metadata_annotation", return_value=True):
            request = factory.post(
                f"/api/anonymization/{video_file.id}/validate/",
                data=data,
                format="json",
            )
            force_authenticate(request, user=user)

            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=video_file.id)

            assert response.status_code == status.HTTP_200_OK

    def test_validate_video_records_derived_metrics_without_patient_values(
        self, factory, user, video_file
    ):
        sensitive_meta = SensitiveMeta.objects.create(
            center=video_file.center,
            patient_first_name="MetricFirst",
            patient_last_name="MetricBeforeSurname",
            patient_dob=timezone.make_aware(datetime(1994, 3, 21)),
            examination_date=date(2024, 2, 15),
            casenumber="CASE-BEFORE",
        )
        video_file.sensitive_meta = sensitive_meta
        video_file.save(update_fields=["sensitive_meta"])
        data = {
            "patient_first_name": "MetricFirst",
            "patient_last_name": "MetricAfterSurname",
            "patient_dob": "21.03.1994",
            "examination_date": "15.02.2024",
            "casenumber": "CASE-AFTER",
            "file_type": "video",
            "no_more_names_confirmed": True,
        }

        with patch.object(VideoFile, "validate_metadata_annotation", return_value=True):
            request = factory.post(
                f"/api/anonymization/{video_file.id}/validate/",
                data=data,
                format="json",
            )
            force_authenticate(request, user=user)

            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=video_file.id)

        assert response.status_code == status.HTTP_200_OK
        metric = AnonymizationValidationMetric.objects.get(video=video_file)
        assert metric.media_type == "video"
        assert metric.no_more_names_confirmed is True
        assert metric.total_fields == len(AnonymizationMetricField.values)
        assert metric.changed_fields >= 2

        last_name_metric = AnonymizationFieldMetric.objects.get(
            validation_metric=metric,
            field_name=AnonymizationMetricField.PATIENT_LAST_NAME,
        )
        assert last_name_metric.present_before is True
        assert last_name_metric.present_after is True
        assert last_name_metric.changed is True
        assert last_name_metric.exact_match is False

        first_name_metric = AnonymizationFieldMetric.objects.get(
            validation_metric=metric,
            field_name=AnonymizationMetricField.PATIENT_FIRST_NAME,
        )
        assert first_name_metric.exact_match is True

        persisted_text = str(metric.__dict__) + str(
            list(
                AnonymizationFieldMetric.objects.filter(
                    validation_metric=metric
                ).values()
            )
        )
        assert "MetricBeforeSurname" not in persisted_text
        assert "MetricAfterSurname" not in persisted_text
        assert "CASE-BEFORE" not in persisted_text
        assert "CASE-AFTER" not in persisted_text

    def test_validate_video_failure(self, factory, user, video_file):
        """Test video validation failure."""
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "21.03.1994",
            "examination_date": "15.02.2024",
            "casenumber": "12345",
            "file_type": "video",
        }

        with patch.object(
            VideoFile, "validate_metadata_annotation", return_value=False
        ):
            request = factory.post(
                f"/api/anonymization/{video_file.id}/validate/",
                data=data,
                format="json",
            )
            force_authenticate(request, user=user)

            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=video_file.id)
            payload = self._response_data(response)
            error_text = self._payload_text(payload, "error")

            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert "Video validation failed" in error_text

    def test_validate_pdf_with_german_dates(self, factory, user, pdf_file):
        """Test validating report with German date format."""
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "21.03.1994",
            "examination_date": "15.02.2024",
            "patient_gender": "männlich",
            "casenumber": "12345",
            "anonymized_text": "Anonymized report content",
            "file_type": "pdf",
            "document_type": "report_final",
        }

        with patch(
            "endoreg_db.views.anonymization.validate.validate_report_metadata_annotation",
            return_value=True,
        ):
            request = factory.post(
                f"/api/anonymization/{pdf_file.id}/validate/",
                data=data,
                format="json",
            )
            force_authenticate(request, user=user)

            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=pdf_file.id)
            payload = self._response_data(response)
            message = self._payload_text(payload, "message")

            assert response.status_code == status.HTTP_200_OK
            assert "report validated" in message
            pdf_file.refresh_from_db()
            assert pdf_file.anonymized_text == "Anonymized report content"
            assert pdf_file.anonym_examination_report_id is not None
            assert isinstance(pdf_file.raw_meta, dict)
            assert pdf_file.raw_meta["document_type"] == "report_final"
            assert (
                pdf_file.raw_meta["pseudo_examination_id"]
                == pdf_file.sensitive_meta.pseudo_examination_id
            )
            assert payload["anonymized_text_saved"] is True
            assert payload["report_file"]["id"] == pdf_file.anonym_examination_report_id
            assert payload["case_resolution"]["status"] == "linked"
            assert (
                payload["validation_context"]["pseudo_examination_id"]
                == pdf_file.sensitive_meta.pseudo_examination_id
            )
            metric = AnonymizationValidationMetric.objects.get(pdf=pdf_file)
            assert metric.media_type == "pdf"
            assert metric.document_type == "report_final"
            assert metric.total_fields == len(AnonymizationMetricField.values)
            assert AnonymizationFieldMetric.objects.filter(
                validation_metric=metric,
                field_name=AnonymizationMetricField.DOCUMENT_TYPE,
                present_after=True,
            ).exists()

    def test_validate_pdf_persists_report_materialization_metadata_in_mocked_validator_path(
        self, factory, user, pdf_file
    ):
        """
        Regression test:
        Even in the mocked validator path, pdf validation should persist report
        materialization and return structured case-resolution metadata.
        """
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "21.03.1994",
            "examination_date": "15.02.2024",
            "patient_gender": "männlich",
            "casenumber": "12345",
            "anonymized_text": "Validated text without explicit case linkage",
            "file_type": "pdf",
            "document_type": "report_final",
        }

        with patch(
            "endoreg_db.views.anonymization.validate.validate_report_metadata_annotation",
            return_value=True,
        ):
            request = factory.post(
                f"/api/anonymization/{pdf_file.id}/validate/",
                data=data,
                format="json",
            )
            force_authenticate(request, user=user)

            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=pdf_file.id)

            assert response.status_code == status.HTTP_200_OK

        pdf_file.refresh_from_db()
        assert pdf_file.anonym_examination_report_id is not None
        assert response.data["case_resolution"]["status"] in {
            "linked",
            "unresolved",
            "ambiguous",
        }

    def test_validate_pdf_failure(self, factory, user, pdf_file):
        """Test report validation failure."""
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "21.03.1994",
            "examination_date": "15.02.2024",
            "casenumber": "12345",
            "file_type": "pdf",
            "document_type": "report_final",
        }

        with patch(
            "endoreg_db.views.anonymization.validate.validate_report_metadata_annotation",
            return_value=False,
        ):
            request = factory.post(
                f"/api/anonymization/{pdf_file.id}/validate/",
                data=data,
                format="json",
            )
            force_authenticate(request, user=user)

            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=pdf_file.id)
            payload = self._response_data(response)
            error_text = self._payload_text(payload, "error")

            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert "report validation failed" in error_text

    def test_validate_pdf_requires_document_type(self, factory, user, pdf_file):
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "21.03.1994",
            "examination_date": "15.02.2024",
            "casenumber": "12345",
            "anonymized_text": "Anonymized report content",
            "file_type": "pdf",
        }

        with patch(
            "endoreg_db.views.anonymization.validate.validate_report_metadata_annotation",
            return_value=True,
        ) as validate_mock:
            request = factory.post(
                f"/api/anonymization/{pdf_file.id}/validate/",
                data=data,
                format="json",
            )
            force_authenticate(request, user=user)

            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=pdf_file.id)
            payload = self._response_data(response)

            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert "document_type is required" in self._payload_text(payload, "error")
            assert payload["allowed_document_types"]
            validate_mock.assert_not_called()

    def test_validate_pdf_missing_document_type_does_not_mutate_metadata(
        self, factory, user, pdf_file
    ):
        sensitive_meta = SensitiveMeta.objects.create(
            center=pdf_file.center,
            patient_first_name="Original",
            patient_last_name="Person",
            validation_comment="keep",
        )
        pdf_file.sensitive_meta = sensitive_meta
        pdf_file.raw_meta = {"existing": "value"}
        pdf_file.anonymized_text = "Original text"
        pdf_file.save(update_fields=["sensitive_meta", "raw_meta", "anonymized_text"])

        def mutate_if_called(instance, _payload):
            instance.sensitive_meta.patient_first_name = "Mutated"
            instance.sensitive_meta.validation_comment = "mutated"
            instance.sensitive_meta.save(
                update_fields=["patient_first_name", "validation_comment"]
            )
            instance.sensitive_meta.get_or_create_state()
            instance.raw_meta = {"document_type": "report_final"}
            instance.anonymized_text = "Mutated text"
            instance.save(update_fields=["raw_meta", "anonymized_text"])
            instance.get_or_create_state().mark_anonymization_validated()
            return True

        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "21.03.1994",
            "examination_date": "15.02.2024",
            "casenumber": "12345",
            "anonymized_text": "Anonymized report content",
            "file_type": "pdf",
        }

        with patch(
            "endoreg_db.views.anonymization.validate.validate_report_metadata_annotation",
            side_effect=mutate_if_called,
        ) as validate_mock:
            request = factory.post(
                f"/api/anonymization/{pdf_file.id}/validate/",
                data=data,
                format="json",
            )
            force_authenticate(request, user=user)

            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=pdf_file.id)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        validate_mock.assert_not_called()
        sensitive_meta.refresh_from_db()
        pdf_file.refresh_from_db()
        assert sensitive_meta.patient_first_name == "Original"
        assert sensitive_meta.validation_comment == "keep"
        assert pdf_file.raw_meta == {"existing": "value"}
        assert pdf_file.anonymized_text == "Original text"
        assert pdf_file.state_id is None

    def test_validate_pdf_failure_rolls_back_metadata_mutations(
        self, factory, user, pdf_file
    ):
        sensitive_meta = SensitiveMeta.objects.create(
            center=pdf_file.center,
            patient_first_name="Original",
            patient_last_name="Person",
            validation_comment="keep",
        )
        pdf_file.sensitive_meta = sensitive_meta
        pdf_file.raw_meta = {"existing": "value"}
        pdf_file.anonymized_text = "Original text"
        pdf_file.save(update_fields=["sensitive_meta", "raw_meta", "anonymized_text"])

        def mutate_and_fail(instance, _payload):
            instance.sensitive_meta.patient_first_name = "Mutated"
            instance.sensitive_meta.validation_comment = "mutated"
            instance.sensitive_meta.save(
                update_fields=["patient_first_name", "validation_comment"]
            )
            instance.sensitive_meta.get_or_create_state()
            instance.raw_meta = {"document_type": "report_final"}
            instance.anonymized_text = "Mutated text"
            instance.save(update_fields=["raw_meta", "anonymized_text"])
            instance.get_or_create_state().mark_anonymization_validated()
            return False

        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "21.03.1994",
            "examination_date": "15.02.2024",
            "casenumber": "12345",
            "anonymized_text": "Anonymized report content",
            "file_type": "pdf",
            "document_type": "report_final",
        }

        with patch(
            "endoreg_db.views.anonymization.validate.validate_report_metadata_annotation",
            side_effect=mutate_and_fail,
        ) as validate_mock:
            request = factory.post(
                f"/api/anonymization/{pdf_file.id}/validate/",
                data=data,
                format="json",
            )
            force_authenticate(request, user=user)

            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=pdf_file.id)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        validate_mock.assert_called_once()
        sensitive_meta.refresh_from_db()
        pdf_file.refresh_from_db()
        assert sensitive_meta.patient_first_name == "Original"
        assert sensitive_meta.validation_comment == "keep"
        assert pdf_file.raw_meta == {"existing": "value"}
        assert pdf_file.anonymized_text == "Original text"
        assert pdf_file.state_id is None

    def test_validate_pdf_rejects_unsupported_document_type(
        self, factory, user, pdf_file
    ):
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "21.03.1994",
            "examination_date": "15.02.2024",
            "casenumber": "12345",
            "anonymized_text": "Anonymized report content",
            "file_type": "pdf",
            "document_type": "unsupported_type",
        }

        with override("en"):
            with patch(
                "endoreg_db.views.anonymization.validate.validate_report_metadata_annotation",
                return_value=True,
            ) as validate_mock:
                request = factory.post(
                    f"/api/anonymization/{pdf_file.id}/validate/",
                    data=data,
                    format="json",
                )
                force_authenticate(request, user=user)

                view = AnonymizationValidateView.as_view()
                response = self._call_view(view, request, file_id=pdf_file.id)
                payload = self._response_data(response)

                assert response.status_code == status.HTTP_400_BAD_REQUEST
                assert "document_type" in payload
                assert "not a valid choice" in self._payload_text(
                    payload, "document_type"
                )
                validate_mock.assert_not_called()

    def test_validate_video_keeps_is_verified_false(self, factory, user, video_file):
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "21.03.1994",
            "examination_date": "15.02.2024",
            "casenumber": "12345",
            "patient_gender": "male",
            "is_verified": False,
            "file_type": "video",
        }

        def check_payload(payload):
            assert payload.get("is_verified") is False
            return True

        with patch.object(
            VideoFile, "validate_metadata_annotation", side_effect=check_payload
        ):
            request = factory.post(
                f"/api/anonymization/{video_file.id}/validate/",
                data=data,
                format="json",
            )
            force_authenticate(request, user=user)

            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=video_file.id)

            assert response.status_code == status.HTTP_200_OK

    def test_validate_video_injects_center_name_and_drops_unknown_gender(
        self, factory, user, video_file
    ):
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "21.03.1994",
            "examination_date": "15.02.2024",
            "casenumber": "12345",
            "patient_gender": "not-a-gender",
            "file_type": "video",
        }

        def check_payload(payload):
            assert payload.get("center_name") == "Test Center"
            assert "patient_gender" not in payload
            return True

        with patch.object(
            VideoFile, "validate_metadata_annotation", side_effect=check_payload
        ):
            request = factory.post(
                f"/api/anonymization/{video_file.id}/validate/",
                data=data,
                format="json",
            )
            force_authenticate(request, user=user)

            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=video_file.id)

            assert response.status_code == status.HTTP_200_OK

    def test_validate_video_persists_tags_and_validation_comment(
        self, factory, user, video_file
    ):
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "21.03.1994",
            "examination_date": "15.02.2024",
            "casenumber": "12345",
            "file_type": "video",
            "tags": ["Nochmal Überprüfen", "Ausgeschlossen", "Nochmal Überprüfen"],
            "validation_comment": "Bitte vor Freigabe nochmal ansehen.",
        }

        with patch.object(VideoFile, "validate_metadata_annotation", return_value=True):
            request = factory.post(
                f"/api/anonymization/{video_file.id}/validate/",
                data=data,
                format="json",
            )
            force_authenticate(request, user=user)

            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=video_file.id)

            assert response.status_code == status.HTTP_200_OK

        video_file.refresh_from_db()
        assert video_file.sensitive_meta is not None
        assert video_file.sensitive_meta.validation_comment == (
            "Bitte vor Freigabe nochmal ansehen."
        )
        assert set(video_file.sensitive_meta.tags.values_list("name", flat=True)) == {
            "Nochmal Überprüfen",
            "Ausgeschlossen",
        }
        assert Tag.objects.filter(name="Nochmal Überprüfen").exists()

    def test_validate_pdf_records_operation_with_expected_metadata(
        self, factory, user, pdf_file
    ):
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "21.03.1994",
            "examination_date": "15.02.2024",
            "patient_gender": "männlich",
            "casenumber": "12345",
            "anonymized_text": "Anonymized report content",
            "file_type": "pdf",
            "document_type": "report_final",
        }

        with (
            patch(
                "endoreg_db.views.anonymization.validate.validate_report_metadata_annotation",
                return_value=True,
            ),
            patch(
                "endoreg_db.views.anonymization.validate.record_operation"
            ) as record_operation_mock,
        ):
            request = factory.post(
                f"/api/anonymization/{pdf_file.id}/validate/",
                data=data,
                format="json",
            )
            force_authenticate(request, user=user)

            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=pdf_file.id)

            assert response.status_code == status.HTTP_200_OK
            record_operation_mock.assert_called_once()
            _, kwargs = record_operation_mock.call_args
            assert kwargs["action"] == "anonymization.validated"
            assert kwargs["resource_type"] == "pdf"
            assert kwargs["resource_id"] == pdf_file.id
            assert kwargs["meta"]["timestamp_source"] == "manual_examination_date"
            assert kwargs["meta"]["examination_date"] == "2024-02-15"

    def test_validate_nonexistent_file(self, factory, user):
        """Test validating non-existent file."""
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "21.03.1994",
            "examination_date": "15.02.2024",
            "casenumber": "12345",
            "patient_gender": "männlich",
        }

        request = factory.post(
            "/api/anonymization/99999/validate/",
            data=data,
            format="json",
        )
        force_authenticate(request, user=user)

        view = AnonymizationValidateView.as_view()
        response = self._call_view(view, request, file_id=99999)
        payload = self._response_data(response)
        error_text = self._payload_text(payload, "error")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "not found" in error_text

    def test_validate_invalid_date_format(self, factory, user, video_file):
        """Test validation with invalid date format."""
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "invalid-date-format",
            "examination_date": "15.02.2024",
            "casenumber": "12345",
        }

        request = factory.post(
            f"/api/anonymization/{video_file.id}/validate/",
            data=data,
            format="json",
        )
        force_authenticate(request, user=user)

        view = AnonymizationValidateView.as_view()
        response = self._call_view(view, request, file_id=video_file.id)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        payload = self._response_data(response)
        invalid_error = payload.get("patient_dob")
        assert invalid_error

    def test_validate_with_is_verified_default(self, factory, user, video_file):
        """Test that is_verified defaults to True."""
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "21.03.1994",
            "examination_date": "15.02.2024",
            "casenumber": "12345",
            "patient_gender": "male",
        }

        def check_is_verified(payload):
            assert payload.get("is_verified") is True
            return True

        with patch.object(
            VideoFile, "validate_metadata_annotation", side_effect=check_is_verified
        ):
            request = factory.post(
                f"/api/anonymization/{video_file.id}/validate/",
                data=data,
                format="json",
            )
            force_authenticate(request, user=user)

            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=video_file.id)

            assert response.status_code == status.HTTP_200_OK

    def test_validate_video_without_file_type_specified(
        self, factory, user, video_file
    ):
        """Test validation tries video first when file_type not specified."""
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "21.03.1994",
            "examination_date": "15.02.2024",
            "casenumber": "12345",
        }

        with patch.object(VideoFile, "validate_metadata_annotation", return_value=True):
            request = factory.post(
                f"/api/anonymization/{video_file.id}/validate/",
                data=data,
                format="json",
            )
            force_authenticate(request, user=user)

            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=video_file.id)
            payload = self._response_data(response)
            message = self._payload_text(payload, "message")

            assert response.status_code == status.HTTP_200_OK
            assert "Video validated" in message

    def test_validate_empty_payload(self, factory, user, video_file):
        """Empty payload should be rejected by serializer requirements."""
        data = {}

        request = factory.post(
            f"/api/anonymization/{video_file.id}/validate/",
            data=data,
            format="json",
        )
        force_authenticate(request, user=user)

        view = AnonymizationValidateView.as_view()
        response = self._call_view(view, request, file_id=video_file.id)
        payload = self._response_data(response)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "patient_first_name" in payload

    def test_validate_with_all_fields(self, factory, user, video_file):
        """Test validation with all possible fields."""
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "21.03.1994",
            "examination_date": "15.02.2024",
            "casenumber": "12345",
            "patient_gender": "M",
            "center_name": "Custom Center",
            "is_verified": True,
            "file_type": "video",
        }

        with patch.object(VideoFile, "validate_metadata_annotation", return_value=True):
            request = factory.post(
                f"/api/anonymization/{video_file.id}/validate/",
                data=data,
                format="json",
            )
            force_authenticate(request, user=user)

            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=video_file.id)

            assert response.status_code == status.HTTP_200_OK

    def test_validate_video_type_missing_video_returns_not_found(self, factory, user):
        """Explicit video requests should not fall back to reports when video is missing."""
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "21.03.1994",
            "examination_date": "15.02.2024",
            "casenumber": "12345",
            "file_type": "video",
        }

        request = factory.post(
            "/api/anonymization/9999/validate/",
            data=data,
            format="json",
        )
        force_authenticate(request, user=user)

        with patch.object(
            RawPdfFile.objects,
            "filter",
            side_effect=AssertionError("report lookup should not occur"),
        ):
            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=9999)
            payload = self._response_data(response)
            error_text = self._payload_text(payload, "error")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "Video 9999 not found" in error_text

    def test_validate_pdf_type_missing_pdf_returns_not_found(self, factory, user):
        """Explicit report requests should not fall back to videos when report is missing."""
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "21.03.1994",
            "examination_date": "15.02.2024",
            "casenumber": "12345",
            "file_type": "pdf",
        }

        request = factory.post(
            "/api/anonymization/8888/validate/",
            data=data,
            format="json",
        )
        force_authenticate(request, user=user)

        with patch.object(
            VideoFile.objects,
            "filter",
            side_effect=AssertionError("Video lookup should not occur"),
        ):
            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=8888)
            payload = self._response_data(response)
            error_text = self._payload_text(payload, "error")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "report 8888 not found" in error_text

    def test_validate_video_exception_returns_server_error(
        self, factory, user, video_file
    ):
        """Exceptions during video validation should surface as server errors."""
        data = {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "21.03.1994",
            "examination_date": "15.02.2024",
            "casenumber": "12345",
        }

        with patch.object(
            VideoFile,
            "validate_metadata_annotation",
            side_effect=RuntimeError("boom"),
        ):
            request = factory.post(
                f"/api/anonymization/{video_file.id}/validate/",
                data=data,
                format="json",
            )
            force_authenticate(request, user=user)

            view = AnonymizationValidateView.as_view()
            response = self._call_view(view, request, file_id=video_file.id)
            payload = self._response_data(response)
            error_text = self._payload_text(payload, "error")

            assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "unexpected error" in error_text
