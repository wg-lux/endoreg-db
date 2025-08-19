"""
PDF import service module.

Provides high-level functions for importing and anonymizing PDF files,
combining RawPdfFile creation with text extraction and anonymization.
"""
from datetime import date
import logging
import sys
import os
import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Union
from contextlib import contextmanager
from django.db import transaction
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.state.raw_pdf import RawPdfState
from endoreg_db.models import SensitiveMeta
from endoreg_db.utils.paths import PDF_DIR, STORAGE_DIR

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from endoreg_db.models import RawPdfFile


class PdfImportService:
    """
    Service class for importing and processing PDF files with text extraction and anonymization.
    """
    
    def __init__(self, allow_meta_overwrite: bool = False):
        """
        Initialize the PDF import service.
        
        Args:
            project_root: Path to the project root directory
            allow_meta_overwrite: Whether to allow overwriting existing SensitiveMeta fields
        """
        self.processed_files = set()
        self._report_reader_available = None
        self._report_reader_class = None
        self.allow_meta_overwrite = allow_meta_overwrite
        
    @contextmanager
    def _file_lock(self, path: Path):
        """Create a file lock to prevent duplicate processing."""
        lock_path = Path(str(path) + ".lock")
        fd = None
        try:
            # atomic create; fail if exists
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            os.write(fd, b"lock")
            os.close(fd)
            fd = None
            yield
        finally:
            try:
                if fd is not None:
                    os.close(fd)
                if lock_path.exists():
                    lock_path.unlink()
            except OSError:
                pass
    
    def _sha256(self, path: Path, chunk: int = 1024 * 1024) -> str:
        """Compute SHA256 hash of a file."""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                b = f.read(chunk)
                if not b:
                    break
                h.update(b)
        return h.hexdigest()
    
    def _quarantine(self, source: Path) -> Path:
        """Move file to quarantine directory to prevent re-processing."""
        qdir = PDF_DIR / "_processing"
        qdir.mkdir(parents=True, exist_ok=True)
        target = qdir / source.name
        # atomic rename on same filesystem
        source.rename(target)
        return target
    
    def _ensure_state(self, pdf_file: "RawPdfFile"):
        """Ensure PDF file has a state object."""
        if getattr(pdf_file, "state", None):
            return pdf_file.state
        if hasattr(pdf_file, "get_or_create_state"):
            state = pdf_file.get_or_create_state()
            pdf_file.state = state
            return state
        # Very defensive fallback
        try:
            state, _ = pdf_file.get_or_create_state(raw_pdf_file=pdf_file)
            pdf_file.state = state
            return state
        except Exception:
            return None
        
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
            # Optional: honor LX_ANONYMIZER_PATH=/abs/path/to/src
            import importlib
            extra = os.getenv("LX_ANONYMIZER_PATH")
            if extra and extra not in sys.path and Path(extra).exists():
                sys.path.insert(0, extra)
                try:
                    mod = importlib.import_module("lx_anonymizer")
                    ReportReader = getattr(mod, "ReportReader")
                    logger.info("Imported lx_anonymizer.ReportReader via LX_ANONYMIZER_PATH")
                    self._report_reader_available = True
                    self._report_reader_class = ReportReader
                    return True, ReportReader
                except Exception as e:
                    logger.warning("Failed importing lx_anonymizer via LX_ANONYMIZER_PATH: %s", e)
                finally:
                    # Keep path for future imports if it worked; otherwise remove.
                    if "ReportReader" not in locals() and extra in sys.path:
                        sys.path.remove(extra)
        
        self._report_reader_available = False
        self._report_reader_class = None
        return False, None

    def _create_simple_anonymized_pdf(
        self, 
        original_pdf_path: Path, 
        anonymized_pdf_path: Path, 
        _original_text: str,  # Prefix with underscore to indicate unused
        anonymized_text: str
    ) -> bool:
        """
        Create a simple anonymized PDF by copying the original and storing anonymized text reference.
        This is a fallback method when advanced PDF anonymization is not available.
        
        Args:
            original_pdf_path: Path to the original PDF
            anonymized_pdf_path: Path where anonymized PDF should be saved
            original_text: Original extracted text
            anonymized_text: Anonymized text
            
        Returns:
            bool: True if anonymized PDF was created successfully
        """
        try:
            import shutil
            
            # Simple approach: copy the original PDF and add a text file with anonymized content
            shutil.copy2(str(original_pdf_path), str(anonymized_pdf_path))
            
            # Create a companion text file with anonymized content
            anonymized_text_path = anonymized_pdf_path.parent / f"{anonymized_pdf_path.stem}_anonymized_text.txt"
            with open(anonymized_text_path, 'w', encoding='utf-8') as f:
                f.write(anonymized_text)
            
            logger.info(f"Created simple anonymized PDF copy: {anonymized_pdf_path}")
            logger.info(f"Created anonymized text file: {anonymized_text_path}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to create simple anonymized PDF: {e}")
            return False

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

    def _move_processed_files_to_storage(self, original_file_path: Path, pdf_file: "RawPdfFile", metadata: dict):
        """
        Move processed PDF files from raw_pdfs to appropriate storage directories.
        
        Args:
            original_file_path: Original file path in raw_pdfs
            pdf_file: RawPdfFile instance
            metadata: Processing metadata
        """
        import shutil
        
        try:
            # Define target directories
            pdfs_dir = PDF_DIR
            anonymized_pdfs_dir = pdfs_dir / 'anonymized'  # fix path join
            cropped_regions_target = pdfs_dir / 'cropped_regions'
            
            # Create target directories
            pdfs_dir.mkdir(parents=True, exist_ok=True)
            anonymized_pdfs_dir.mkdir(parents=True, exist_ok=True)
            cropped_regions_target.mkdir(parents=True, exist_ok=True)
            
            original_file_path = Path(original_file_path)
            pdf_name = original_file_path.stem
            pdfs_dir = PDF_DIR
            
            # 1. Move the original processed PDF to pdfs directory, then delete source
            if original_file_path.exists():
                target_pdf_path = pdfs_dir / f"{pdf_name}.pdf"
                shutil.move(str(original_file_path), str(target_pdf_path))
                logger.info(f"Moved original PDF to: {target_pdf_path}")
                
                # Delete the original raw PDF file after successful copy
                try:
                    original_file_path.unlink()
                    logger.info(f"Deleted original raw PDF file: {original_file_path}")
                except OSError as e:
                    logger.warning(f"Failed to delete original raw PDF file {original_file_path}: {e}")
            
            # 2. Move anonymized PDF if it exists next to the original (legacy)
            legacy_anonymized = original_file_path.parent / f"{pdf_name}_anonymized.pdf"
            if legacy_anonymized.exists():
                target_anonymized_path = anonymized_pdfs_dir / f"{pdf_name}_anonymized.pdf"
                shutil.move(str(legacy_anonymized), str(target_anonymized_path))
                logger.info(f"Moved anonymized PDF to: {target_anonymized_path}")
            
            # 3. Move cropped regions if they exist next to the original (legacy)
            legacy_crops = original_file_path.parent / 'cropped_regions'
            if legacy_crops.exists():
                for crop_file in legacy_crops.glob(f"{pdf_name}*"):
                    target_crop_path = cropped_regions_target / crop_file.name
                    shutil.move(str(crop_file), str(target_crop_path))
                    logger.info(f"Moved cropped region to: {target_crop_path}")
                
                try:
                    if not any(legacy_crops.iterdir()):
                        legacy_crops.rmdir()
                        logger.debug(f"Removed empty directory: {legacy_crops}")
                except OSError:
                    pass  # Directory not empty, that's fine
            
            logger.info(f"Successfully moved all processed files for {pdf_name}")
            
            
        except Exception as e:
            logger.error(f"Error moving processed files: {e}")
            raise

    def import_and_anonymize(
        self, 
        file_path: Union[Path, str], 
        center_name: str, 
        processor_name: str = None,
        delete_source: bool = False,
        retry: bool = False,
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
        
        # Acquire file lock and check for duplicates
        with self._file_lock(file_path):
            # Compute hash early to check DB idempotently
            try:
                file_hash = self._sha256(file_path)
            except Exception:
                file_hash = None      
            if file_hash and RawPdfFile.objects.filter(pdf_hash=file_hash).exists():
                existing = RawPdfFile.objects.get(pdf_hash=file_hash)

                # delete source only if it's a different, non-canonical location
                try:
                    if Path(file_path).resolve() != Path(existing.file.path).resolve():
                        os.remove(file_path)
                except Exception:
                    logger.warning("Could not remove duplicate source %s", file_path, exc_info=True)

                if not existing.text:
                    logger.info(f"Reusing existing RawPdfFile {existing.pdf_hash} with no text")
                    try:
                        retry = True
                        self.import_and_anonymize(
                            file_path=existing.file.path,
                            center_name=existing.center.name if existing.center else "unknown_center",
                            processor_name=existing.processor_name,
                            delete_source=False,
                            retry=retry
                        )
                        retry = False
                    except Exception as e:
                        logger.error(f"Failed to re-import existing PDF {existing.pdf_hash}: {e}")
                        
                        try:
                            self._create_simple_anonymized_pdf(
                                original_pdf_path=Path(existing.file.path),
                                anonymized_pdf_path=Path(existing.file.path).parent / f"{existing.pdf_hash}_anonymized.pdf",
                                _original_text=existing.text,
                                anonymized_text=existing.anonymized_text
                            )
                        except Exception as e:
                            logger.error(f"Failed to create simple anonymized PDF for existing file: {e}")
                existing.state.mark_sensitive_meta_processed()
                return existing
            
            # Move to quarantine directory so watcher no longer sees it
            try:
                qpath = self._quarantine(file_path)
                logger.debug("Quarantined input to %s", qpath)
            except Exception as e:
                logger.warning("Quarantine move failed (%s); continuing.", e)
        
        # Step 1: Create RawPdfFile instance
        logger.info("Creating RawPdfFile instance...")
        from django.db import IntegrityError
        try:
            if not retry:
                pdf_file = RawPdfFile.create_from_file_initialized(
                    file_path=qpath,
                    center_name=center_name,
                    delete_source=delete_source,
                )
            else:
                # If retrying, we assume the file already exists and we just need to fetch it
                pdf_file = RawPdfFile.objects.get(pdf_hash=file_hash)
                logger.info(f"Retrying import for existing RawPdfFile {pdf_file.pdf_hash}")
            state = pdf_file.get_or_create_state()
            state.mark_processing_started()
        except IntegrityError:
            # Another worker won the race; fetch existing
            if file_hash:
                pdf_file = RawPdfFile.objects.get(pdf_hash=file_hash)
                logger.info("Race condition detected, using existing RawPdfFile")
            else:
                raise
        
        if not pdf_file:
            raise RuntimeError("Failed to create RawPdfFile instance")
        
        logger.info(f"Created RawPdfFile with hash: {pdf_file.pdf_hash}")
        
        # Handle processor_name if supported
        if processor_name and hasattr(pdf_file, "processor_name"):
            pdf_file.processor_name = processor_name
            pdf_file.save(update_fields=["processor_name"])
            logger.debug("Set processor_name: %s", processor_name)
        
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
                
                # Ensure output dirs under PDF_DIR exist for crops and anonymized PDFs
                crops_dir = PDF_DIR / 'cropped_regions'
                crops_dir.mkdir(parents=True, exist_ok=True)
                anonymized_dir = PDF_DIR / 'anonymized'
                anonymized_dir.mkdir(parents=True, exist_ok=True)

                # Initialize ReportReader with proper configuration
                report_reader = ReportReader(
                    report_root_path=STORAGE_DIR,
                    locale="de_DE",  # German locale for medical data
                    text_date_format="%d.%m.%Y"  # German date format
                )
                

                
                # Use the process_report_with_cropping method for comprehensive processing
                # Direct crops to PDF_DIR/cropped_regions
                original_text, anonymized_text, extracted_metadata, cropped_regions = report_reader.process_report_with_cropping(
                    pdf_path=qpath,
                    crop_sensitive_regions=True,
                    crop_output_dir=str(crops_dir),
                    anonymization_output_dir=str(anonymized_dir)
                )
                                
                # Compute expected anonymized path under PDF_DIR/anonymized
                pdf_stem = Path(pdf_file.file.path).stem
                expected_anonymized_path = anonymized_dir / f"{pdf_stem}_anonymized.pdf"
                if expected_anonymized_path.exists():
                    logger.info(f"Anonymized PDF automatically created at: {expected_anonymized_path}")
                elif cropped_regions:
                    logger.info(f"Cropped regions detected; anonymized PDF not found at: {expected_anonymized_path}")
                else:
                    logger.info("No cropped regions detected - anonymized PDF may not be needed")

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
                                
                                # Configurable overwrite policy
                                should_overwrite = (
                                    self.allow_meta_overwrite
                                    or not old_value
                                    or old_value in ['Patient', 'Unknown']
                                )
                                if new_value and should_overwrite:
                                    setattr(sm, sm_field, new_value)
                                    updated_fields.append(sm_field)
                        
                        if updated_fields:
                            sm.save()
                            logger.info(f"Updated SensitiveMeta fields: {updated_fields}")
                    
                    # Store anonymized text if available
                    if anonymized_text and anonymized_text != original_text:
                        # Set anonymization flag
                        pdf_file.anonymized = True
                        logger.info("PDF text anonymization completed")
                        
                        # Create anonymized PDF file
                        try:
                            original_pdf_path = Path(pdf_file.file.path)
                            # Prüfe nur noch Pfad unter PDF_DIR/anonymized
                            if expected_anonymized_path.exists():
                                logger.info(f"Anonymized PDF already exists from process_report_with_cropping: {expected_anonymized_path}")
                                anonymized_pdf_created = True
                            else:
                                # Try different methods to create anonymized PDF
                                anonymized_pdf_created = False
                            
                            # Method 1: Use ReportReader's sensitive_region_cropper for PDF anonymization
                            if hasattr(report_reader, 'sensitive_cropper') and cropped_regions:
                                try:
                                    crop_output_dir = str(crops_dir)
                                    report_reader.sensitive_cropper.create_anonymized_pdf_with_crops(
                                        pdf_path=str(original_pdf_path),
                                        crop_output_dir=crop_output_dir,
                                        anonymized_pdf_path=str(expected_anonymized_path)
                                    )
                                    anonymized_pdf_created = expected_anonymized_path.exists()
                                    logger.info("Used ReportReader.sensitive_cropper.create_anonymized_pdf_with_crops method (PDF_DIR)")
                                except Exception as e:
                                    logger.warning("ReportReader.sensitive_cropper.create_anonymized_pdf_with_crops failed: %s", e)
                            
                            # Method 2: Fallback - create simple text replacement PDF into PDF_DIR/anonymized
                            if not anonymized_pdf_created:
                                try:
                                    self._create_simple_anonymized_pdf(
                                        original_pdf_path, 
                                        expected_anonymized_path, 
                                        original_text, 
                                        anonymized_text
                                    )
                                    anonymized_pdf_created = expected_anonymized_path.exists()
                                    logger.info("Used fallback simple anonymized PDF creation (PDF_DIR)")
                                except Exception as e:
                                    logger.warning("Fallback anonymized PDF creation failed: %s", e)
                            
                            if anonymized_pdf_created and expected_anonymized_path.exists():
                                logger.info("Successfully created anonymized PDF: %s", expected_anonymized_path)
                                
                                # Store the anonymized PDF path in the model with proper FileField handling
                                if hasattr(pdf_file, 'anonymized_file'):
                                    from django.core.files.base import File
                                    try:
                                        field = pdf_file._meta.get_field('anonymized_file')
                                        if getattr(field, 'upload_to', None) is not None:
                                            with open(expected_anonymized_path, "rb") as fh:
                                                pdf_file.anonymized_file.save(
                                                    expected_anonymized_path.name, File(fh), save=False
                                                )
                                        else:
                                            pdf_file.anonymized_file = str(expected_anonymized_path)
                                    except Exception as e:
                                        logger.warning("FileField handling failed, using string path: %s", e)
                                        pdf_file.anonymized_file = str(expected_anonymized_path)
                            else:
                                logger.warning("All methods failed to create anonymized PDF at %s", expected_anonymized_path)
                        except Exception as pdf_error:
                            logger.error("Error creating anonymized PDF: %s", pdf_error)
                            # Don't fail the entire process, continue with text anonymization
                    
                    # Update state tracking
                    state = self._ensure_state(pdf_file)
                    state.mark_anonymized()

                    # Save all changes
                    with transaction.atomic():
                        pdf_file.save()
                
                else:
                    logger.warning("No text could be extracted from PDF %s", pdf_file.pdf_hash)
                    
                    # Even if no text was extracted, mark as ready for validation
                    state = self._ensure_state(pdf_file)
                    if state:
                        state.text_meta_extracted = False  # No text was extracted
                        state.pdf_meta_extracted = False   # No metadata extracted
                        state.sensitive_meta_processed = False 
                        state.save()
                        logger.info("Set PDF state: processed=False for validation (status='done')")
                    
                    # Save changes
                    with transaction.atomic():
                        pdf_file.save()
                    
            except Exception as e:
                logger.warning("Text processing failed: %s", e)
                # Don't raise - continue with unprocessed PDF but mark as ready for validation
                
                # Mark as ready for validation even if text processing failed
                state = self._ensure_state(pdf_file)
                if state:
                    state.text_meta_extracted = False  # Text processing failed
                    state.pdf_meta_extracted = False   # No metadata extracted
                    state.sensitive_meta_processed = False  # Keep for validation (status='done')
                    state.save()
                    logger.info("Set PDF state: processed=False despite text processing failure (status='done')")
                
                # Save changes
                with transaction.atomic():
                    pdf_file.save()
                
        elif not report_reading_available:
            logger.warning("Report reading not available (lx_anonymizer not found)")
            
            # Even without ReportReader, mark as ready for validation
            state = self._ensure_state(pdf_file)
            if state:
                state.text_meta_extracted = False  # No text extraction without ReportReader
                state.pdf_meta_extracted = False   # No metadata extraction
                state.sensitive_meta_processed = False  # Keep for validation (status='done')
                state.save()
                logger.info("Set PDF state: processed=False despite no ReportReader (status='done')")
            
            # Save changes
            with transaction.atomic():
                pdf_file.save()

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

        # Step 5: Move processed files to correct directories
        logger.info("Moving processed files to target directories...")
        try:
            # Get report metadata if available
            metadata = {}
            if 'extracted_metadata' in locals():
                metadata = extracted_metadata
            
            self._move_processed_files_to_storage(file_path, pdf_file, metadata)
        except Exception as e:
            logger.warning(f"Failed to move processed files: {e}")

        # Step 6: Refresh from database and return
        with transaction.atomic():
            pdf_file.refresh_from_db()

        logger.info(f"Import and processing completed for RawPdfFile hash: {pdf_file.pdf_hash}")
        os.removedirs(qpath.parent)  # Clean up quarantine directory if empty
        os.removedirs(PDF_DIR / 'cropped_regions')  # Clean up cropped regions directory if empty
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
        
        # Ensure state exists and mark as ready for validation in simple import
        state = self._ensure_state(pdf_file)
        if state:
            state.text_meta_extracted = False  # No text processing in simple import
            state.pdf_meta_extracted = False   # No metadata extraction
            state.sensitive_meta_processed = False  # Keep for validation (status='done')
            state.save()
            logger.info("Set PDF state: processed=False for simple import (status='done')")
        
        # Save changes
        with transaction.atomic():
            pdf_file.save()
        
        logger.info("Simple import completed for RawPdfFile hash: %s", pdf_file.pdf_hash)
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