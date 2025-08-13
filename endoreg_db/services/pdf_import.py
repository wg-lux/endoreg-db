"""
PDF import service module.

Provides high-level functions for importing and anonymizing PDF files,
combining RawPdfFile creation with text extraction and anonymization.
"""
from datetime import date
import logging
import sys
from pathlib import Path
from turtle import st
from typing import TYPE_CHECKING, Union
from django.db import transaction
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models import SensitiveMeta

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from endoreg_db.models import RawPdfFile


class PdfImportService:
    """
    Service class for importing and processing PDF files with text extraction and anonymization.
    """
    
    def __init__(self, project_root: Path):
        """
        Initialize the PDF import service.
        
        Args:
            project_root: Path to the project root directory
        """
        self.project_root = Path(project_root)
        self.processed_files = set()
        self._report_reader_available = None
        self._report_reader_class = None
        
    def _ensure_report_reading_available(self):
        """
        Ensure report reading modules are available by adding lx-anonymizer to path.
        
        Returns:
            Tuple of (availability_flag, ReportReader_class)
        """
        if self._report_reader_available is not None:
            return self._report_reader_available, self._report_reader_class
            
        try:
            # Try direct import first
            from lx_anonymizer import ReportReader
            
            logger.info("Successfully imported lx_anonymizer ReportReader module")
            self._report_reader_available = True
            self._report_reader_class = ReportReader
            return True, ReportReader
            
        except ImportError:
            try:
                # Check if we can find the lx-anonymizer directory
                from importlib import resources
                lx_anonymizer_path = resources.files("lx_anonymizer")
                
                if lx_anonymizer_path.exists():
                    # Add to Python path temporarily
                    if str(lx_anonymizer_path) not in sys.path:
                        sys.path.insert(0, str(lx_anonymizer_path))
                    
                    # Try import again
                    from lx_anonymizer import ReportReader
                    
                    logger.info("Successfully imported lx_anonymizer ReportReader module via path manipulation")
                    
                    # Remove from path to avoid conflicts
                    if str(lx_anonymizer_path) in sys.path:
                        sys.path.remove(str(lx_anonymizer_path))
                        
                    self._report_reader_available = True
                    self._report_reader_class = ReportReader
                    return True, ReportReader
                
                else:
                    logger.warning(f"lx-anonymizer path not found: {lx_anonymizer_path}")
                    
            except Exception as e:
                logger.warning(f"Report reading not available: {e}")
        
        self._report_reader_available = False
        self._report_reader_class = None
        return False, None

    def _ensure_default_patient_data(self, pdf_file: "RawPdfFile") -> None:
        """
        Ensure PDF has minimum required patient data in SensitiveMeta.
        Creates default values if data is missing after text processing.
        """
        if not pdf_file.sensitive_meta:
            logger.info(f"No SensitiveMeta found for PDF {pdf_file.pdf_hash}, creating default")
            
            # Create default SensitiveMeta with placeholder data
            default_data = {
                "patient_first_name": "Patient",
                "patient_last_name": "Unknown", 
                "patient_dob": date(1990, 1, 1),  # Default DOB
                "examination_date": date.today(),
                "center_name": pdf_file.center.name if pdf_file.center else "university_hospital_wuerzburg"
            }
            
            try:
                sensitive_meta = SensitiveMeta.create_from_dict(default_data)
                pdf_file.sensitive_meta = sensitive_meta
                pdf_file.save(update_fields=['sensitive_meta'])
                logger.info(f"Created default SensitiveMeta for PDF {pdf_file.pdf_hash}")
            except Exception as e:
                logger.error(f"Failed to create default SensitiveMeta for PDF {pdf_file.pdf_hash}: {e}")

    def import_and_anonymize(
        self, 
        file_path: Union[Path, str], 
        center_name: str, 
        processor_name: str = None,
        delete_source: bool = False
    ) -> "RawPdfFile":
        """
        Import a PDF file and anonymize it using ReportReader.
        
        Args:
            file_path: Path to the PDF file to import
            center_name: Name of the center to associate with PDF
            processor_name: Name of the processor (optional for PDFs)
            delete_source: Whether to delete the source file after import
            
        Returns:
            RawPdfFile instance after import and processing
            
        Raises:
            Exception: On any failure during import or processing
        """
        file_path = Path(file_path)
        
        # Check if the file has already been processed
        if str(file_path) in self.processed_files:
            logger.info(f"File {file_path} already processed, skipping")
            return None
        
        if not file_path.exists():
            raise FileNotFoundError(f"PDF file not found: {file_path}")
            
        logger.info(f"Starting import and processing for: {file_path}")
        
        # Step 1: Create RawPdfFile instance
        logger.info("Creating RawPdfFile instance...")
        pdf_file = RawPdfFile.create_from_file_initialized(
            file_path=file_path,
            center_name=center_name,
            delete_source=delete_source,
        )
        
        if not pdf_file:
            raise RuntimeError("Failed to create RawPdfFile instance")
        
        logger.info(f"Created RawPdfFile with hash: {pdf_file.pdf_hash}")
        
        # Mark the file as processed
        self.processed_files.add(str(file_path))
        
        # Step 2: Ensure default patient data BEFORE text processing
        logger.info("Ensuring default patient data before text processing...")
        self._ensure_default_patient_data(pdf_file)
        
        # Step 3: Text extraction and anonymization using ReportReader
        report_reading_available, ReportReader = self._ensure_report_reading_available()
        
        if report_reading_available and pdf_file.file:
            try:
                logger.info("Starting text extraction and metadata processing with ReportReader...")
                
                # Initialize ReportReader with proper configuration
                report_reader = ReportReader(
                    report_root_path=str(Path(pdf_file.file.path).parent),
                    locale="de_DE",  # German locale for medical data
                    text_date_format="%d.%m.%Y"  # German date format
                )
                
                # Use the process_report method for comprehensive processing
                # This method handles text extraction, metadata extraction, and anonymization
                original_text, anonymized_text, extracted_metadata = report_reader.process_report(
                    pdf_path=pdf_file.file.path,
                    use_ensemble=False,  # Set to True if you want better OCR
                    verbose=True,
                    use_llm_extractor='deepseek'  # Use DeepSeek for metadata extraction
                )
                
                if original_text:
                    # Store extracted text in the PDF file
                    pdf_file.text = original_text
                    logger.info(f"Extracted {len(original_text)} characters of text from PDF")
                    
                    # Update SensitiveMeta with extracted metadata
                    if pdf_file.sensitive_meta and extracted_metadata:
                        sm = pdf_file.sensitive_meta
                        
                        # Map ReportReader metadata to SensitiveMeta fields
                        metadata_mapping = {
                            'patient_first_name': 'patient_first_name',
                            'patient_last_name': 'patient_last_name', 
                            'patient_dob': 'patient_dob',
                            'examination_date': 'examination_date',
                            'examiner_first_name': 'examiner_first_name',
                            'examiner_last_name': 'examiner_last_name',
                            'endoscope_type': 'endoscope_type',
                            'casenumber': 'case_number'
                        }
                        
                        # Update fields with extracted information
                        updated_fields = []
                        for meta_key, sm_field in metadata_mapping.items():
                            if extracted_metadata.get(meta_key) and hasattr(sm, sm_field):
                                old_value = getattr(sm, sm_field)
                                new_value = extracted_metadata[meta_key]
                                
                                # Only update if we have new, non-empty information
                                if new_value and (not old_value or old_value in ['Patient', 'Unknown']):
                                    setattr(sm, sm_field, new_value)
                                    updated_fields.append(sm_field)
                        
                        if updated_fields:
                            sm.save()
                            logger.info(f"Updated SensitiveMeta fields: {updated_fields}")
                    
                    # Store anonymized text if available
                    if anonymized_text and anonymized_text != original_text:
                        # You might want to store this in a separate field or use it differently
                        pdf_file.anonymized = True
                        logger.info("PDF text anonymization completed")
                    
                    # Update state tracking
                    if pdf_file.state:
                        pdf_file.state.text_extracted = True
                        pdf_file.state.metadata_extracted = bool(extracted_metadata)
                        pdf_file.state.anonymized = pdf_file.anonymized
                        pdf_file.state.save()
                    
                    # Save all changes
                    with transaction.atomic():
                        pdf_file.save()
                
                else:
                    logger.warning(f"No text could be extracted from PDF {pdf_file.pdf_hash}")
                    
            except Exception as e:
                logger.warning(f"Text processing failed: {e}")
                # Don't raise - continue with unprocessed PDF
                
        elif not report_reading_available:
            logger.warning("Report reading not available (lx_anonymizer not found)")

        # Step 4: Signal completion
        logger.info("Signaling import and processing completion...")
        try:
            # Optional: Add completion tracking flags if the model supports them
            completion_fields = []
            for field_name in ['import_completed', 'processing_complete', 'ready_for_validation']:
                if hasattr(pdf_file, field_name):
                    setattr(pdf_file, field_name, True)
                    completion_fields.append(field_name)

            if completion_fields:
                pdf_file.save(update_fields=completion_fields)
                logger.info(f"Updated completion flags: {completion_fields}")

        except Exception as e:
            logger.warning(f"Failed to signal completion status: {e}")

        # Step 5: Refresh from database and return
        with transaction.atomic():
            pdf_file.refresh_from_db()

        logger.info(f"Import and processing completed for RawPdfFile hash: {pdf_file.pdf_hash}")
        return pdf_file

    def import_simple(
        self, 
        file_path: Union[Path, str], 
        center_name: str,
        delete_source: bool = False
    ) -> "RawPdfFile":
        """
        Simple PDF import without text processing or anonymization.
        
        Args:
            file_path: Path to the PDF file to import
            center_name: Name of the center to associate with PDF
            delete_source: Whether to delete the source file after import
            
        Returns:
            RawPdfFile instance after basic import
        """
        file_path = Path(file_path)
        
        # Check if the file has already been processed
        if str(file_path) in self.processed_files:
            logger.info(f"File {file_path} already processed, skipping")
            return None
            
        if not file_path.exists():
            raise FileNotFoundError(f"PDF file not found: {file_path}")
            
        logger.info(f"Starting simple import for: {file_path}")
        
        # Create RawPdfFile instance
        pdf_file = RawPdfFile.create_from_file_initialized(
            file_path=file_path,
            center_name=center_name,
            delete_source=delete_source,
        )
        
        if not pdf_file:
            raise RuntimeError("Failed to create RawPdfFile instance")
        
        # Mark the file as processed
        self.processed_files.add(str(file_path))
        
        logger.info(f"Simple import completed for RawPdfFile hash: {pdf_file.pdf_hash}")
        return pdf_file
    
    def check_storage_capacity(self, file_path: Union[Path, str], storage_root, min_required_space) -> None:
        """
        Check if there is sufficient storage capacity for the PDF file.
        
        Args:
            file_path: Path to the PDF file to check
            
        Raises:
            InsufficientStorageError: If there is not enough space
        """
        import shutil
        from endoreg_db.exceptions import InsufficientStorageError
        
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found for storage check: {file_path}")
        
        # Get the size of the file
        file_size = file_path.stat().st_size
        
        # Get available space in the storage directory

        total, used, free = shutil.disk_usage(storage_root)
        
        if file_size:
            min_required_space = file_size if isinstance(min_required_space, int) else 0

        # Check if there is enough space
        if file_size > free:
            raise InsufficientStorageError(f"Not enough space to store PDF file: {file_path}")
        logger.info(f"Storage check passed for {file_path}: {file_size} bytes, {free} bytes available")
        
        return True