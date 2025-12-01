"""
Test that PDF status is correctly updated to "ready for validation" after anonymization.
"""

from unittest.mock import patch

import pytest

from endoreg_db.models import Center
from endoreg_db.models.state.anonymization import AnonymizationState
from endoreg_db.services.pdf_import import PdfImportService


@pytest.mark.django_db
class TestPdfStatusAfterAnonymization:
    """Test that PDF state is correctly updated after successful anonymization."""

    @pytest.fixture
    def center(self):
        """
        Create and return a test Center instance for use in tests.
        
        Returns:
            Center: A newly created Center with name "test_center" and display_name "Test Center".
        """
        return Center.objects.create(
            name="test_center",
            display_name="Test Center",
        )

    def test_status_becomes_done_after_anonymization(self, center, tmp_path):
        """
        Test that after successful PDF anonymization, the status becomes 'done_processing_anonymization'
        (ready for validation) instead of remaining at 'not_started'.
        """
        # Create a test PDF file
        test_pdf = tmp_path / "test_report.pdf"
        test_pdf.write_bytes(b"%PDF-1.4\n%test content")

        # Create anonymized PDF file that will be referenced
        anonymized_pdf_path = tmp_path / "anonymized.pdf"
        anonymized_pdf_path.write_bytes(b"%PDF-1.4\n%anonymized")

        # Mock the text processing to simulate successful anonymization
        def mock_process(service_instance):
            # Simulate successful processing by setting context flags
            """
            Populate the given service instance's processing_context to simulate successful text extraction and anonymization.
            
            Parameters:
                service_instance: The PDF import/anonymization service instance whose `processing_context` dict will be modified. After calling, the context contains:
                    - "text_extracted": True
                    - "anonymization_completed": True
                    - "original_text": original text content
                    - "anonymized_text": anonymized text content
                    - "anonymized_pdf_path": string path to the anonymized PDF
            """
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
            # Import the PDF
            service = PdfImportService.with_blackening()
            pdf = service.import_and_anonymize(
                file_path=test_pdf, center_name=center.name, delete_source=False
            )

            # Verify PDF was created
            assert pdf is not None

            # Verify state exists
            state = pdf.get_or_create_state()
            assert state is not None

            # Verify anonymization flags are set
            assert state.anonymized is True, "PDF should be marked as anonymized"
            assert state.sensitive_meta_processed is True, (
                "PDF should be marked as ready for validation"
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
        Verify that in blackening mode a PDF's anonymization state progresses to ready-for-validation.
        
        After importing and anonymizing a test PDF in blackening mode, assert that the PDF's state has anonymization_status set to AnonymizationState.DONE_PROCESSING_ANONYMIZATION and that both `anonymized` and `sensitive_meta_processed` are True.
        """
        test_pdf = tmp_path / "test_report.pdf"
        test_pdf.write_bytes(b"%PDF-1.4\n%test content")

        anonymized_pdf_path = tmp_path / "anonymized.pdf"
        anonymized_pdf_path.write_bytes(b"%PDF-1.4\n%anonymized")

        def mock_process(service_instance):
            """
            Simulate a successful extraction and anonymization run by updating the service instance's processing context to indicate completion and provide the anonymized PDF path.
            
            Parameters:
                service_instance: The import/anonymization service instance whose `processing_context` will be updated. The function sets:
                    - `text_extracted` to `True`
                    - `anonymization_completed` to `True`
                    - `anonymized_pdf_path` to the precomputed anonymized PDF path (string)
            """
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
        test_pdf.write_bytes(b"%PDF-1.4\n%test content")

        anonymized_pdf_path = tmp_path / "anonymized.pdf"
        anonymized_pdf_path.write_bytes(b"%PDF-1.4\n%anonymized")

        def mock_process(service_instance):
            """
            Simulate a successful extraction and anonymization run by updating the service instance's processing context to indicate completion and provide the anonymized PDF path.
            
            Parameters:
                service_instance: The import/anonymization service instance whose `processing_context` will be updated. The function sets:
                    - `text_extracted` to `True`
                    - `anonymization_completed` to `True`
                    - `anonymized_pdf_path` to the precomputed anonymized PDF path (string)
            """
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