"""
Test that report status is correctly updated to "ready for validation" after anonymization.
"""

from unittest.mock import patch

import pytest

from endoreg_db.models import Center
from endoreg_db.models.state.anonymization import AnonymizationState
from endoreg_db.services.pdf_import import PdfImportService


@pytest.mark.django_db
class TestPdfStatusAfterAnonymization:
    """Test that report state is correctly updated after successful anonymization."""

    @pytest.fixture
    def center(self):
        """Create test center."""
        return Center.objects.create(
            name="test_center",
            display_name="Test Center",
        )

    def test_status_becomes_done_after_anonymization(self, center, tmp_path):
        """
        Test that after successful report anonymization, the status becomes 'done_processing_anonymization'
        (ready for validation) instead of remaining at 'not_started'.
        """
        # Create a test report file
        test_pdf = tmp_path / "test_report.pdf"
        test_pdf.write_bytes(b"%report-1.4\n%test content")

        # Create anonymized report file that will be referenced
        anonymized_pdf_path = tmp_path / "anonymized.pdf"
        anonymized_pdf_path.write_bytes(b"%report-1.4\n%anonymized")

        # Mock the text processing to simulate successful anonymization
        def mock_process(service_instance):
            # Simulate successful processing by setting context flags
            service_instance.processing_context["text_extracted"] = True
            service_instance.processing_context["anonymization_completed"] = True
            service_instance.processing_context["original_text"] = (
                "Original text content"
            )
            service_instance.processing_context["anonymized_text"] = (
                "Anonymized text content"
            )
            service_instance.processing_context["anonymized_pdf_path"] = str(
                anonymized_pdf_path
            )

        with patch.object(PdfImportService, "_process_text_and_metadata", mock_process):
            # Import the report
            service = PdfImportService.with_blackening()
            pdf = service.import_and_anonymize(
                file_path=test_pdf, center_name=center.name, delete_source=False
            )

            # Verify report was created
            assert pdf is not None

            # Verify state exists
            state = pdf.get_or_create_state()
            assert state is not None

            # Verify anonymization flags are set
            assert state.anonymized is True, "report should be marked as anonymized"
            assert state.sensitive_meta_processed is True, (
                "report should be marked as ready for validation"
            )

            # Verify status is DONE_PROCESSING_ANONYMIZATION (ready for validation)
            status = state.anonymization_status
            assert status == AnonymizationState.DONE_PROCESSING_ANONYMIZATION, (
                f"Expected status DONE_PROCESSING_ANONYMIZATION, got {status}"
            )

            # Verify the status string representation
            assert status.value == "done_processing_anonymization", (
                f"Expected 'done_processing_anonymization', got '{status.value}'"
            )

    def test_status_progression_blackening_mode(self, center, tmp_path):
        """
        Test the full status progression for blackening mode:
        not_started -> processing -> done -> (validation ready)
        """
        test_pdf = tmp_path / "test_report.pdf"
        test_pdf.write_bytes(b"%report-1.4\n%test content")

        anonymized_pdf_path = tmp_path / "anonymized.pdf"
        anonymized_pdf_path.write_bytes(b"%report-1.4\n%anonymized")

        def mock_process(service_instance):
            service_instance.processing_context["text_extracted"] = True
            service_instance.processing_context["anonymization_completed"] = True
            service_instance.processing_context["anonymized_pdf_path"] = str(
                anonymized_pdf_path
            )

        with patch.object(PdfImportService, "_process_text_and_metadata", mock_process):
            service = PdfImportService.with_blackening()

            # Import and check initial status
            pdf = service.import_and_anonymize(
                file_path=test_pdf, center_name=center.name
            )

            state = pdf.get_or_create_state()

            # After successful processing, status should be DONE_PROCESSING_ANONYMIZATION
            assert (
                state.anonymization_status
                == AnonymizationState.DONE_PROCESSING_ANONYMIZATION
            )
            assert state.anonymized is True
            assert state.sensitive_meta_processed is True

    def test_status_progression_cropping_mode(self, center, tmp_path):
        """
        Test the full status progression for cropping mode.
        """
        test_pdf = tmp_path / "test_report.pdf"
        test_pdf.write_bytes(b"%report-1.4\n%test content")

        anonymized_pdf_path = tmp_path / "anonymized.pdf"
        anonymized_pdf_path.write_bytes(b"%report-1.4\n%anonymized")

        def mock_process(service_instance):
            service_instance.processing_context["text_extracted"] = True
            service_instance.processing_context["anonymization_completed"] = True
            service_instance.processing_context["anonymized_pdf_path"] = str(
                anonymized_pdf_path
            )

        with patch.object(PdfImportService, "_process_text_and_metadata", mock_process):
            service = PdfImportService.with_cropping()

            pdf = service.import_and_anonymize(
                file_path=test_pdf, center_name=center.name
            )

            state = pdf.get_or_create_state()

            # After successful processing, status should be DONE_PROCESSING_ANONYMIZATION
            assert (
                state.anonymization_status
                == AnonymizationState.DONE_PROCESSING_ANONYMIZATION
            )
            assert state.anonymized is True
            assert state.sensitive_meta_processed is True
