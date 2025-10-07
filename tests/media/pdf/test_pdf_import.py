"""
Test module for PDF import service functionality.

The goal is that after processing:
- An original PDF with file_path in /data/pdfs/sensitive
- No PDF should remain in /data/raw_pdfs
- PDF should be processed and anonymized
"""
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

from django.test import TestCase

from endoreg_db.services.pdf_import import PdfImportService
from endoreg_db.models import Center


class TestPdfImportFileMovement(TestCase):
    """Test PDF import service file movement and organization."""
    
    def setUp(self):
        """Set up test environment."""
        # Create minimal PDF file data (PDF header)
        self.test_pdf_data = b'%PDF-1.4\n1 0 obj\n<<\n/Type /Catalog\n/Pages 2 0 R\n>>\nendobj\n2 0 obj\n<<\n/Type /Pages\n/Kids [3 0 R]\n/Count 1\n>>\nendobj\n3 0 obj\n<<\n/Type /Page\n/Parent 2 0 R\n/MediaBox [0 0 612 792]\n>>\nendobj\nxref\n0 4\n0000000000 65535 f\n0000000009 00000 n\n0000000074 00000 n\n0000000120 00000 n\ntrailer\n<<\n/Size 4\n/Root 1 0 R\n>>\nstartxref\n202\n%%EOF'
        
        # Create temporary directories for testing
        self.temp_storage = Path(tempfile.mkdtemp())
        self.temp_raw_pdfs = self.temp_storage / 'raw_pdfs'
        self.temp_pdfs = self.temp_storage / 'pdfs'
        self.temp_pdfs_sensitive = self.temp_pdfs / 'sensitive'
        
        # Create all directories
        for dir_path in [self.temp_raw_pdfs, self.temp_pdfs, self.temp_pdfs_sensitive]:
            dir_path.mkdir(parents=True, exist_ok=True)
        
        # Create test center
        self.center = Center.objects.create(
            name="test_center",
            display_name="Test Center"
        )
    
    def tearDown(self):
        """Clean up test environment."""
        if self.temp_storage.exists():
            shutil.rmtree(self.temp_storage)
    
    def create_test_pdf_file(self, filename: str = "test_document.pdf") -> Path:
        """Create a test PDF file in raw_pdfs directory."""
        pdf_path = self.temp_raw_pdfs / filename
        with open(pdf_path, 'wb') as f:
            f.write(self.test_pdf_data)
        return pdf_path
    
    @patch('endoreg_db.utils.paths.PDF_DIR')
    def test_pdf_file_movement_flow(self, mock_pdf_dir):
        """Test complete PDF file movement flow."""
        # Mock PDF_DIR to use our temp directory
        mock_pdf_dir.__str__ = lambda: str(self.temp_pdfs)
        mock_pdf_dir.__truediv__ = lambda self, other: self.temp_pdfs / other
        mock_pdf_dir.return_value = self.temp_pdfs
        
        # Create test PDF file
        test_pdf_path = self.create_test_pdf_file("test_input.pdf")
        self.assertTrue(test_pdf_path.exists(), "Test PDF file should be created")
        
        # Mock report reading to avoid dependencies
        with patch.object(PdfImportService, '_ensure_report_reading_available') as mock_report_reading:
            mock_report_reading.return_value = (False, None)
            
            # Mock PDF creation methods
            with patch('endoreg_db.models.media.pdf.raw_pdf.RawPdfFile.objects.create') as mock_create_pdf:
                mock_pdf = MagicMock()
                mock_pdf.pdf_hash = "test-hash-123"
                mock_pdf.file = MagicMock()
                mock_pdf.file.path = str(self.temp_pdfs_sensitive / "test-hash-123.pdf")
                mock_pdf.sensitive_meta = None
                mock_pdf.center = self.center
                mock_create_pdf.return_value = mock_pdf
                
                # Mock state management
                mock_state = MagicMock()
                mock_pdf.get_or_create_state = MagicMock(return_value=mock_state)
                mock_pdf.save = MagicMock()
                
                # Mock file operations
                with patch('shutil.move'):
                    with patch('os.remove'):
                        # Initialize service and run import
                        service = PdfImportService()
                        
                        result_pdf = service.import_and_anonymize(
                            file_path=test_pdf_path,
                            center_name=self.center.name,
                            delete_source=True
                        )
                        
                        # Verify the result
                        self.assertIsNotNone(result_pdf, "PDF import should return a PDF instance")
        
        # Verify file operations were called correctly
        # Original file should be processed and moved to sensitive directory
    
    @patch('endoreg_db.utils.paths.PDF_DIR')  
    def test_sensitive_file_creation(self, mock_pdf_dir):
        """Test that PDF files are moved to sensitive directory."""
        mock_pdf_dir.__str__ = lambda: str(self.temp_pdfs)
        mock_pdf_dir.__truediv__ = lambda self, other: self.temp_pdfs / other
        
        test_pdf_path = self.create_test_pdf_file("sensitive_test.pdf")
        
        # Mock dependencies
        with patch.object(PdfImportService, '_ensure_report_reading_available') as mock_report_reading:
            mock_report_reading.return_value = (False, None)
            
            with patch('endoreg_db.models.media.pdf.raw_pdf.RawPdfFile.objects.create') as mock_create_pdf:
                mock_pdf = MagicMock()
                mock_pdf.pdf_hash = "sensitive-hash-456"
                mock_pdf.file = MagicMock()
                mock_pdf.sensitive_meta = None
                mock_pdf.center = self.center
                mock_create_pdf.return_value = mock_pdf
                
                mock_state = MagicMock()
                mock_pdf.get_or_create_state = MagicMock(return_value=mock_state)
                mock_pdf.save = MagicMock()
                
                service = PdfImportService()
                
                # Mock the create_sensitive_file method to track calls
                with patch.object(service, 'create_sensitive_file') as mock_create_sensitive:
                    service.import_and_anonymize(
                        file_path=test_pdf_path,
                        center_name=self.center.name
                    )
                    
                    # Verify sensitive file creation was called
                    mock_create_sensitive.assert_called()
    
    @patch('endoreg_db.utils.paths.PDF_DIR')
    def test_hash_based_naming(self, mock_pdf_dir):
        """Test that PDF files are named using their content hash."""
        mock_pdf_dir.__str__ = lambda: str(self.temp_pdfs)
        mock_pdf_dir.__truediv__ = lambda self, other: self.temp_pdfs / other
        
        test_pdf_path = self.create_test_pdf_file("original_name.pdf")
        
        # Test the _sha256 method directly
        service = PdfImportService()
        pdf_hash = service._sha256(test_pdf_path)
        
        # Hash should be consistent for the same content
        self.assertIsInstance(pdf_hash, str)
        self.assertEqual(len(pdf_hash), 64)  # SHA256 hex string length
        
        # Same file should produce same hash
        pdf_hash_2 = service._sha256(test_pdf_path)
        self.assertEqual(pdf_hash, pdf_hash_2, "Same file should produce same hash")
    
    @patch('endoreg_db.utils.paths.PDF_DIR')
    def test_error_handling_preserves_file_structure(self, mock_pdf_dir):
        """Test that errors during processing don't leave files in wrong locations."""
        mock_pdf_dir.__str__ = lambda: str(self.temp_pdfs)
        mock_pdf_dir.__truediv__ = lambda self, other: self.temp_pdfs / other
        
        test_pdf_path = self.create_test_pdf_file("error_test.pdf")
        
        # Mock report reading to fail
        with patch.object(PdfImportService, '_ensure_report_reading_available') as mock_report_reading:
            mock_report_reading.return_value = (False, None)
            
            # Mock PDF creation to fail
            with patch('endoreg_db.models.media.pdf.raw_pdf.RawPdfFile.objects.create') as mock_create_pdf:
                mock_create_pdf.side_effect = Exception("Simulated creation error")
                
                service = PdfImportService()
                
                # Import should fail gracefully
                with self.assertRaises(Exception):
                    service.import_and_anonymize(
                        file_path=test_pdf_path,
                        center_name=self.center.name
                    )
        
        # Error handling should not create orphaned files
        # The exact location depends on where the error occurred
        total_files = (
            len(list(self.temp_raw_pdfs.glob("*"))) +
            len(list(self.temp_pdfs.glob("*"))) + 
            len(list(self.temp_pdfs_sensitive.glob("*")))
        )
        
        # Should have at most 1 file total
        self.assertLessEqual(total_files, 1,
                           "Error handling should not create duplicate files")
    
    def test_file_lock_mechanism(self):
        """Test that file locking prevents duplicate processing."""
        test_pdf_path = self.create_test_pdf_file("lock_test.pdf")
        lock_path = Path(str(test_pdf_path) + ".lock")
        
        service = PdfImportService()
        
        # Test file lock creation and cleanup
        with service._file_lock(test_pdf_path):
            # Lock file should exist during processing
            self.assertTrue(lock_path.exists(), "Lock file should exist during processing")
        
        # Lock file should be cleaned up after processing
        self.assertFalse(lock_path.exists(), "Lock file should be cleaned up after processing")
    
    def test_quarantine_functionality(self):
        """Test that problematic files are moved to quarantine."""
        test_pdf_path = self.create_test_pdf_file("quarantine_test.pdf")
        
        service = PdfImportService()
        
        # Test quarantine method
        quarantine_path = service._quarantine(test_pdf_path)
        
        # Original file should be moved
        self.assertFalse(test_pdf_path.exists(), "Original file should be moved to quarantine")
        
        # File should exist in quarantine directory
        self.assertTrue(quarantine_path.exists(), "File should exist in quarantine directory")
        self.assertTrue("_processing" in str(quarantine_path), "Quarantine path should contain '_processing'")
    
    @patch('endoreg_db.utils.paths.PDF_DIR')
    def test_storage_capacity_check(self, mock_pdf_dir):
        """Test storage capacity validation."""
        mock_pdf_dir.__str__ = lambda: str(self.temp_pdfs)
        
        test_pdf_path = self.create_test_pdf_file("capacity_test.pdf")
        
        service = PdfImportService()
        
        # Test with sufficient space (should pass)
        try:
            service.check_storage_capacity(
                test_pdf_path, 
                self.temp_storage, 
                min_required_space=100  # 100 bytes minimum
            )
        except Exception as e:
            self.fail(f"Storage capacity check should pass with sufficient space: {e}")
        
        # Test with insufficient space by mocking disk_usage
        with patch('shutil.disk_usage') as mock_disk_usage:
            # Mock very little free space
            mock_disk_usage.return_value = (1000, 950, 50)  # total, used, free (50 bytes free)
            
            from endoreg_db.exceptions import InsufficientStorageError
            with self.assertRaises(InsufficientStorageError):
                service.check_storage_capacity(
                    test_pdf_path,
                    self.temp_storage,
                    min_required_space=1000  # 1000 bytes minimum
                )
    
    def test_processing_context_management(self):
        """Test that processing context is properly managed."""
        service = PdfImportService()
        
        # Context should be empty initially
        self.assertEqual(service.processing_context, {})
        
        # Context should be populated during initialization
        test_pdf_path = self.create_test_pdf_file("context_test.pdf")
        service._initialize_processing_context(
            test_pdf_path, self.center.name, False, False
        )
        
        expected_keys = {'file_path', 'center_name', 'delete_source', 'retry', 
                        'file_hash', 'processing_started', 'text_extracted', 
                        'metadata_processed', 'anonymization_completed'}
        
        self.assertTrue(expected_keys.issubset(service.processing_context.keys()),
                       "Processing context should contain all expected keys")
        
        # Context should be cleaned up
        service._cleanup_processing_context()
        self.assertEqual(service.processing_context, {},
                        "Processing context should be cleaned up")
