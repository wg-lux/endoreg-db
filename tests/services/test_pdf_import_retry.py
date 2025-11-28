"""
Test suite for report Import Service retry functionality with get_raw_file_path().

This test ensures the critical fix for report re-import using raw file paths
instead of sensitive file paths which may have been deleted.
"""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from django.core.files.base import ContentFile

from endoreg_db.models import Center, RawPdfFile
from endoreg_db.services.pdf_import import PdfImportService
from endoreg_db.utils.hashs import get_pdf_hash


@pytest.mark.django_db
class TestPdfImportServiceRetryFix:
    """
    Test suite for report import service retry fixes.

    **Expected Behavior:**
    - Service uses get_raw_file_path() for retries
    - Retry works when sensitive file is deleted
    - Clear error when raw file not found
    """

    @pytest.fixture
    def center(self):
        """Create test center."""
        return Center.objects.create(
            name="university_hospital_wuerzburg",
            display_name="University Hospital Würzburg",
        )

    @pytest.fixture
    def sample_pdf_content(self):
        """Create sample report content."""
        return b"""%report-1.4
1 0 obj
<<
/Type /Catalog
/Pages 2 0 R
>>
endobj
2 0 obj
<<
/Type /Pages
/Kids [3 0 R]
/Count 1
>>
endobj
3 0 obj
<<
/Type /Page
/Parent 2 0 R
/Resources <<
/Font <<
/F1 <<
/Type /Font
/Subtype /Type1
/BaseFont /Helvetica
>>
>>
>>
/MediaBox [0 0 612 792]
/Contents 4 0 R
>>
endobj
4 0 obj
<<
/Length 44
>>
stream
BT
/F1 12 Tf
100 700 Td
(Sample report) Tj
ET
endstream
endobj
xref
0 5
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000315 00000 n
trailer
<<
/Size 5
/Root 1 0 R
>>
startxref
408
%%EOF
"""

    def test_retry_uses_get_raw_file_path(self, center, sample_pdf_content, tmp_path):
        """
        Test that retry processing uses get_raw_file_path() method.

        **Test Scenario:**
        1. Create RawPdfFile with raw file only
        2. Simulate existing report without text (needs reprocessing)
        3. Call _retry_existing_pdf()
        4. Verify it uses get_raw_file_path() to find source file
        """
        # Create raw report file
        raw_file = tmp_path / "test.pdf"
        raw_file.write_bytes(sample_pdf_content)

        pdf_hash = get_pdf_hash(raw_file)

        # Create RawPdfFile with file
        raw_pdf = RawPdfFile.objects.create(
            pdf_hash=pdf_hash,
            center=center,
            text=None,  # No text = needs reprocessing
        )
        raw_pdf.file.save("test.pdf", ContentFile(sample_pdf_content), save=True)

        # Create service
        service = PdfImportService()

        # Mock get_raw_file_path to return our test file
        with patch.object(raw_pdf, "get_raw_file_path", return_value=raw_file):
            # Mock import_and_anonymize to avoid actual processing
            with patch.object(service, "import_and_anonymize") as mock_import:
                mock_import.return_value = raw_pdf

                # Call retry method
                result = service._retry_existing_pdf(raw_pdf)

                # Verify get_raw_file_path was called
                raw_pdf.get_raw_file_path.assert_called_once()

                # Verify import_and_anonymize was called with raw file path
                mock_import.assert_called_once()
                call_args = mock_import.call_args
                assert call_args[1]["file_path"] == raw_file
                assert call_args[1]["delete_source"] is False
                assert call_args[1]["retry"] is True

    def test_retry_handles_missing_raw_file(self, center):
        """
        Test that retry gracefully handles missing raw file.

        **Test Scenario:**
        1. Create RawPdfFile without raw file
        2. Call _retry_existing_pdf()
        3. Verify it returns existing report without error
        4. Verify clear error message logged
        """
        raw_pdf = RawPdfFile.objects.create(
            pdf_hash="missing-raw-file-hash",
            center=center,
            text=None,
        )

        # Create service
        service = PdfImportService()

        # Mock get_raw_file_path to return None (file not found)
        with patch.object(raw_pdf, "get_raw_file_path", return_value=None):
            # Call retry method
            result = service._retry_existing_pdf(raw_pdf)

            # Should return existing report
            assert result == raw_pdf
            assert service.current_pdf == raw_pdf

    def test_retry_handles_deleted_sensitive_file(
        self, center, sample_pdf_content, tmp_path
    ):
        """
        Test that retry works when sensitive file is deleted but raw file exists.

        **Test Scenario (matches production bug):**
        1. Create RawPdfFile with file field pointing to sensitive (deleted)
        2. Raw file still exists in raw_pdfs/
        3. Call _retry_existing_pdf()
        4. Verify it finds and uses raw file for reprocessing
        """
        # Create raw file that still exists
        raw_file = tmp_path / "raw_pdfs" / "original.pdf"
        raw_file.parent.mkdir(parents=True, exist_ok=True)
        raw_file.write_bytes(sample_pdf_content)

        pdf_hash = get_pdf_hash(raw_file)

        # Create RawPdfFile
        raw_pdf = RawPdfFile.objects.create(
            pdf_hash=pdf_hash,
            center=center,
            text=None,
        )

        # Simulate file field pointing to deleted sensitive file
        raw_pdf.file.save("sensitive.pdf", ContentFile(sample_pdf_content), save=True)

        # Mock get_raw_file_path to return the existing raw file
        with patch.object(raw_pdf, "get_raw_file_path", return_value=raw_file):
            # Mock import_and_anonymize
            with patch.object(PdfImportService, "import_and_anonymize") as mock_import:
                mock_import.return_value = raw_pdf

                service = PdfImportService()
                result = service._retry_existing_pdf(raw_pdf)

                # Verify import was called with raw file path (not sensitive path)
                mock_import.assert_called_once()
                call_kwargs = mock_import.call_args[1]
                assert call_kwargs["file_path"] == raw_file, (
                    "Should use raw file path, not sensitive path"
                )

    def test_retry_error_message_clarity(self, center, caplog):
        """
        Test that retry provides clear error message when file not found.

        **Test Scenario:**
        1. Create RawPdfFile without raw file
        2. Call _retry_existing_pdf()
        3. Verify error message tells user to re-upload
        """
        import logging

        raw_pdf = RawPdfFile.objects.create(
            pdf_hash="test-error-message-hash",
            center=center,
        )

        service = PdfImportService()

        # Mock get_raw_file_path to return None
        with patch.object(raw_pdf, "get_raw_file_path", return_value=None):
            with caplog.at_level(logging.ERROR):
                result = service._retry_existing_pdf(raw_pdf)

                # Check error message
                assert any(
                    "Please re-upload the original report file" in record.message
                    for record in caplog.records
                ), "Error message should tell user to re-upload"


@pytest.mark.django_db
class TestPdfImportServiceFileDiscovery:
    """
    Test file discovery in report import scenarios matching production logs.
    """

    @pytest.fixture
    def center(self):
        """Create test center."""
        return Center.objects.create(name="university_hospital_wuerzburg")

    def test_existing_pdf_found_by_hash(self, center, tmp_path):
        """
        Test that existing report is found by hash.

        **Production Scenario:**
        - File appears in raw_pdfs: c8bd695d-6e2c-43a5-ac2c-1e76c33d9caf.pdf
        - System finds existing RawPdfFile with hash: 9fb6a690112961...
        - Different filename but same content
        """
        # Create file with one name
        file1 = tmp_path / "c8bd695d-6e2c-43a5-ac2c-1e76c33d9caf.pdf"
        file1.write_bytes(b"%report-1.4\nTest content\n%%EOF")

        hash1 = get_pdf_hash(file1)

        # Create RawPdfFile
        pdf1 = RawPdfFile.objects.create(
            pdf_hash=hash1,
            center=center,
        )

        # Create file with different name but same content
        file2 = tmp_path / "lux-histo-1.pdf"
        file2.write_bytes(b"%report-1.4\nTest content\n%%EOF")

        hash2 = get_pdf_hash(file2)

        # Hashes should match
        assert hash1 == hash2, "Same content should produce same hash"

        # Query should find existing report
        existing = RawPdfFile.objects.filter(pdf_hash=hash2).first()
        assert existing == pdf1, "Should find existing report by hash"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
