"""
PDF import service module.

Provides high-level functions for importing and anonymizing PDF files,
combining RawPdfFile creation with text extraction and anonymization.
"""
from datetime import date, datetime
import logging
import shutil
import sys
import os
import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Union
from contextlib import contextmanager
from django.conf.locale import tr
from django.db import transaction
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.state.raw_pdf import RawPdfState
from endoreg_db.models import SensitiveMeta
from endoreg_db.utils.paths import PDF_DIR, STORAGE_DIR
import time

logger = logging.getLogger(__name__)

# Treat lock files older than this as stale and reclaim them (in seconds)
STALE_LOCK_SECONDS = 600

if TYPE_CHECKING:
    pass  # RawPdfFile already imported above


class PdfImportService:
    """
    Service class for importing and processing PDF files with text extraction and anonymization.
    Uses a central PDF instance pattern for cleaner state management.
    """
    
    def __init__(self, allow_meta_overwrite: bool = False):
        """
        Initialize the PDF import service.
        
        Args:
            allow_meta_overwrite: Whether to allow overwriting existing SensitiveMeta fields
        """
        self.processed_files = set()
        self._report_reader_available = None
        self._report_reader_class = None
        self.allow_meta_overwrite = allow_meta_overwrite
        
        # Central PDF instance management
        self.current_pdf = None
        self.processing_context = {}
        
    @contextmanager
    def _file_lock(self, path: Path):
        """Create a file lock to prevent duplicate processing.
        Handles stale lock files by reclaiming after STALE_LOCK_SECONDS.
        """
        lock_path = Path(str(path) + ".lock")
        fd = None
        try:
            try:
                # atomic create; fail if exists
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                # Check for stale lock
                age = None
                try:
                    st = os.stat(lock_path)
                    age = time.time() - st.st_mtime
                except FileNotFoundError:
                    # race: lock removed between exists and stat; just retry acquiring below
                    pass

                if age is not None and age > STALE_LOCK_SECONDS:
                    try:
                        logger.warning(
                            "Stale lock detected for %s (age %.0fs). Reclaiming lock...",
                            path, age
                        )
                        lock_path.unlink()
                    except Exception as e:
                        logger.warning("Failed to remove stale lock %s: %s", lock_path, e)
                    # retry acquire
                    fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                else:
                    # Another worker is processing this file
                    raise ValueError(f"File already being processed: {path}")

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


    def _ensure_default_patient_data(self, pdf_instance: "RawPdfFile" = None) -> None:
        """
        Ensure PDF has minimum required patient data in SensitiveMeta.
        Creates default values if data is missing after text processing.
        Uses the central PDF instance if no specific instance provided.
        
        Args:
            pdf_instance: Optional specific PDF instance, defaults to self.current_pdf
        """
        pdf_file = pdf_instance or self.current_pdf
        if not pdf_file:
            logger.warning("No PDF instance available for ensuring default patient data")
            return
            
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
        delete_source: bool = False,
        retry: bool = False,
    ) -> "RawPdfFile":
        """
        Import a PDF file and anonymize it using ReportReader.
        Uses centralized PDF instance management pattern.
        
        Args:
            file_path: Path to the PDF file to import
            center_name: Name of the center to associate with PDF
            delete_source: Whether to delete the source file after import
            retry: Whether this is a retry attempt
            
        Returns:
            RawPdfFile instance after import and processing
            
        Raises:
            Exception: On any failure during import or processing
        """
        try:
            # Initialize processing context
            self._initialize_processing_context(file_path, center_name, delete_source, retry)
            
            # Step 1: Validate and prepare file
            self._validate_and_prepare_file()
            
            # Step 2: Create or retrieve PDF instance
            self._create_or_retrieve_pdf_instance()
            
            # Early return check - if no PDF instance was created, return None
            if not self.current_pdf:
                logger.warning(f"No PDF instance created for {file_path}, returning None")
                return None
            
            # Step 3: Setup processing environment
            self._setup_processing_environment()
            
            # Step 4: Process text and metadata
            self._process_text_and_metadata()
            
            # Step 5: Finalize processing
            self._finalize_processing()
            
            return self.current_pdf
            
        except ValueError as e:
            # Handle "File already being processed" case specifically
            if "already being processed" in str(e):
                logger.info(f"Skipping file {file_path}: {e}")
                return None
            else:
                logger.error(f"PDF import failed for {file_path}: {e}")
                self._cleanup_on_error()
                raise
        except Exception as e:
            logger.error(f"PDF import failed for {file_path}: {e}")
            # Cleanup on error
            self._cleanup_on_error()
            raise
        finally:
            # Always cleanup context
            self._cleanup_processing_context()

    def _initialize_processing_context(self, file_path: Union[Path, str], center_name: str, 
                                     delete_source: bool, retry: bool):
        """Initialize the processing context for the current PDF."""
        self.processing_context = {
            'file_path': Path(file_path),
            'center_name': center_name,
            'delete_source': delete_source,
            'retry': retry,
            'file_hash': None,
            'processing_started': False,
            'text_extracted': False,
            'metadata_processed': False,
            'anonymization_completed': False
        }
        
        # Check if already processed (only during current session to prevent race conditions)
        if str(file_path) in self.processed_files:
            logger.info(f"File {file_path} already being processed in current session, skipping")
            raise ValueError("File already being processed")
        
        logger.info(f"Starting import and processing for: {file_path}")

    def _validate_and_prepare_file(self):
        """Validate file existence and calculate hash."""
        file_path = self.processing_context['file_path']
        
        if not file_path.exists():
            raise FileNotFoundError(f"PDF file not found: {file_path}")
        
        try:
            self.processing_context['file_hash'] = self._sha256(file_path)
        except Exception as e:
            logger.warning(f"Could not calculate file hash: {e}")
            self.processing_context['file_hash'] = None

    def _create_or_retrieve_pdf_instance(self):
        """Create new or retrieve existing PDF instance."""
        file_path = self.processing_context['file_path']
        center_name = self.processing_context['center_name']
        delete_source = self.processing_context['delete_source']
        retry = self.processing_context['retry']
        file_hash = self.processing_context['file_hash']
        
        if not retry:
            # Check for existing PDF and handle duplicates
            with self._file_lock(file_path):
                existing = None
                if file_hash and RawPdfFile.objects.filter(pdf_hash=file_hash).exists():
                    existing = RawPdfFile.objects.get(pdf_hash=file_hash)

                if existing:
                    logger.info(f"Found existing RawPdfFile {existing.pdf_hash}")
                    if existing.text:
                        logger.info(f"Existing PDF {existing.pdf_hash} already processed - returning")
                        self.current_pdf = existing
                        return
                    else:
                        # Retry processing
                        logger.info(f"Reprocessing existing PDF {existing.pdf_hash}")
                        return self._retry_existing_pdf(existing)
        
        # Create new PDF instance
        logger.info("Creating new RawPdfFile instance...")
        from django.db import IntegrityError
        
        try:
            if not retry:
                self.current_pdf = RawPdfFile.create_from_file_initialized(
                    file_path=file_path,
                    center_name=center_name,
                    delete_source=delete_source,
                )
            else:
                # Retrieve existing for retry
                self.current_pdf = RawPdfFile.objects.get(pdf_hash=file_hash)
                logger.info(f"Retrying import for existing RawPdfFile {self.current_pdf.pdf_hash}")
                
                # Check if retry is actually needed
                if self.current_pdf.text:
                    logger.info(f"Existing PDF {self.current_pdf.pdf_hash} already processed during retry - returning")
                    return
            
            if not self.current_pdf:
                raise RuntimeError("Failed to create RawPdfFile instance")
                
            logger.info(f"PDF instance ready: {self.current_pdf.pdf_hash}")
            
        except IntegrityError:
            # Race condition - another worker created it
            if file_hash:
                self.current_pdf = RawPdfFile.objects.get(pdf_hash=file_hash)
                logger.info("Race condition detected, using existing RawPdfFile")
            else:
                raise

    def _setup_processing_environment(self):
        """Setup processing environment and state."""
        # Create sensitive file copy
        self.create_sensitive_file(self.current_pdf, self.processing_context['file_path'])
        
        # Update file path to point to sensitive copy
        self.processing_context['file_path'] = self.current_pdf.file.path
        
        # Ensure state exists
        state = self.current_pdf.get_or_create_state()
        state.mark_processing_started()
        self.processing_context['processing_started'] = True
        
        # Mark as processed to prevent duplicates
        self.processed_files.add(str(self.processing_context['file_path']))
        
        # Ensure default patient data
        logger.info("Ensuring default patient data...")
        self._ensure_default_patient_data(self.current_pdf)

    def _process_text_and_metadata(self):
        """Process text extraction and metadata using ReportReader."""
        report_reading_available, ReportReader = self._ensure_report_reading_available()
        
        if not report_reading_available:
            logger.warning("Report reading not available (lx_anonymizer not found)")
            self._mark_processing_incomplete("no_report_reader")
            return
        
        if not self.current_pdf.file:
            logger.warning("No file available for text processing")
            self._mark_processing_incomplete("no_file")
            return
        
        try:
            logger.info("Starting text extraction and metadata processing with ReportReader...")
            
            # Setup output directories
            crops_dir = PDF_DIR / 'cropped_regions'
            anonymized_dir = PDF_DIR / 'anonymized'
            crops_dir.mkdir(parents=True, exist_ok=True)
            anonymized_dir.mkdir(parents=True, exist_ok=True)

            # Initialize ReportReader
            report_reader = ReportReader(
                report_root_path=STORAGE_DIR,
                locale="de_DE",
                text_date_format="%d.%m.%Y"
            )

            # Process with cropping
            original_text, anonymized_text, extracted_metadata, cropped_regions, anonymized_pdf_path = report_reader.process_report_with_cropping(
                pdf_path=self.processing_context['file_path'],
                crop_sensitive_regions=True,
                crop_output_dir=str(crops_dir),
                anonymization_output_dir=str(anonymized_dir)
            )
            
            # Store results in context
            self.processing_context.update({
                'original_text': original_text,
                'anonymized_text': anonymized_text,
                'extracted_metadata': extracted_metadata,
                'cropped_regions': cropped_regions,
                'anonymized_pdf_path': anonymized_pdf_path
            })
            
            if original_text:
                self._apply_text_results()
                self.processing_context['text_extracted'] = True
                
            if extracted_metadata:
                self._apply_metadata_results()
                self.processing_context['metadata_processed'] = True
                
            if anonymized_pdf_path:
                self._apply_anonymized_pdf()
                self.processing_context['anonymization_completed'] = True
                
        except Exception as e:
            logger.warning(f"Text processing failed: {e}")
            self._mark_processing_incomplete("text_processing_failed")

    def _apply_text_results(self):
        """Apply text extraction results to the PDF instance."""
        if not self.current_pdf:
            logger.warning("Cannot apply text results - no PDF instance available")
            return
            
        original_text = self.processing_context.get('original_text')
        anonymized_text = self.processing_context.get('anonymized_text')
        
        if not original_text:
            logger.warning("No original text available to apply")
            return
        
        # Store extracted text
        self.current_pdf.text = original_text
        logger.info(f"Extracted {len(original_text)} characters of text from PDF")
        
        # Handle anonymized text
        if anonymized_text and anonymized_text != original_text:
            self.current_pdf.anonymized = True
            logger.info("PDF text anonymization completed")

    def _apply_metadata_results(self):
        """Apply metadata extraction results to SensitiveMeta."""
        if not self.current_pdf:
            logger.warning("Cannot apply metadata results - no PDF instance available")
            return
            
        extracted_metadata = self.processing_context.get('extracted_metadata')
        
        if not self.current_pdf.sensitive_meta or not extracted_metadata:
            logger.debug("No sensitive meta or extracted metadata available")
            return
            
        sm = self.current_pdf.sensitive_meta
        
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
                raw_value = extracted_metadata[meta_key]
                
                # Skip if we just got the field name as a string (indicates no actual data)
                if isinstance(raw_value, str) and raw_value == meta_key:
                    continue
                
                # Handle date fields specially
                if sm_field in ['patient_dob', 'examination_date']:
                    new_value = self._parse_date_field(raw_value, meta_key, sm_field)
                    if new_value is None:
                        continue
                else:
                    new_value = raw_value
                
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

    def _parse_date_field(self, raw_value, meta_key, sm_field):
        """Parse date field with error handling."""
        try:
            if isinstance(raw_value, str):
                # Skip if the value is just the field name itself
                if raw_value == meta_key:
                    logger.warning(
                        "Skipping date field %s - got field name '%s' instead of actual date",
                        sm_field, raw_value
                    )
                    return None
                
                # Try common date formats
                date_formats = ['%Y-%m-%d', '%d.%m.%Y', '%d/%m/%Y', '%m/%d/%Y']
                for fmt in date_formats:
                    try:
                        return datetime.strptime(raw_value, fmt).date()
                    except ValueError:
                        continue
                
                logger.warning("Could not parse date '%s' for field %s", raw_value, sm_field)
                return None
                
            elif hasattr(raw_value, 'date'):
                return raw_value.date()
            else:
                return raw_value
                
        except (ValueError, AttributeError) as e:
            logger.warning("Date parsing failed for %s: %s", sm_field, e)
            return None

    # from gc-08
    def _apply_anonymized_pdf(self):
        """
        Attach the already-generated anonymized PDF without copying bytes.

        We do NOT re-upload or re-save file bytes via Django storage (which would
        place a new file under upload_to='raw_pdfs' and retrigger the watcher).
        Instead, we point the FileField to the path that the anonymizer already
        wrote (ideally relative to STORAGE_DIR). Additionally, we make sure the
        model/state reflect that anonymization is done even if text didn't change.
        """
        if not self.current_pdf:
            logger.warning("Cannot apply anonymized PDF - no PDF instance available")
            return

        anonymized_pdf_path = self.processing_context.get('anonymized_pdf_path')
        if not anonymized_pdf_path:
            logger.debug("No anonymized_pdf_path present in processing context")
            return

        anonymized_path = Path(anonymized_pdf_path)
        if not anonymized_path.exists():
            logger.warning("Anonymized PDF path returned but file does not exist: %s", anonymized_path)
            return

        logger.info("Anonymized PDF created by ReportReader at: %s", anonymized_path)

        try:
            # Prefer storing a path relative to STORAGE_DIR so Django serves it correctly
            try:
                relative_name = str(anonymized_path.relative_to(STORAGE_DIR))
            except ValueError:
                # Fallback to absolute path if the file lives outside STORAGE_DIR
                relative_name = str(anonymized_path)

            # Only update if something actually changed
            if getattr(self.current_pdf.anonymized_file, 'name', None) != relative_name:
                self.current_pdf.anonymized_file.name = relative_name

            # Ensure model/state reflect anonymization even if text didn't differ
            if not getattr(self.current_pdf, "anonymized", False):
                self.current_pdf.anonymized = True

            # Persist cropped regions info somewhere useful (optional & non-breaking)
            # If your model has a field for this, persist there; otherwise we just log.
            cropped_regions = self.processing_context.get('cropped_regions')
            if cropped_regions:
                logger.debug("Cropped regions recorded (%d regions).", len(cropped_regions))

            # Save model changes
            update_fields = ['anonymized_file']
            if 'anonymized' in self.current_pdf.__dict__:
                update_fields.append('anonymized')
            self.current_pdf.save(update_fields=update_fields)

            # Mark state as anonymized immediately; this keeps downstream flows working
            state = self._ensure_state(self.current_pdf)
            if state and not state.anonymized:
                state.mark_anonymized(save=True)

            logger.info("Updated anonymized_file reference to: %s", self.current_pdf.anonymized_file.name)

        except Exception as e:
            logger.warning("Could not set anonymized file reference: %s", e)
    
    '''def _apply_anonymized_pdf(self):
        """Apply anonymized PDF results."""
        if not self.current_pdf:
            logger.warning("Cannot apply anonymized PDF - no PDF instance available")
            return
            
        anonymized_pdf_path = self.processing_context.get('anonymized_pdf_path')
        
        if not anonymized_pdf_path:
            return
            
        anonymized_path = Path(anonymized_pdf_path)
        if anonymized_path.exists():
            logger.info(f"Anonymized PDF created by ReportReader at: {anonymized_path}")
            try:
                from django.core.files.base import File
                with open(anonymized_path, 'rb') as f:
                    django_file = File(f)
                    self.current_pdf.anonymized_file.save(
                        anonymized_path.name,
                        django_file,
                        save=False
                    )
            except Exception as e:
                logger.warning(f"Could not set anonymized file reference: {e}")
        else:
            logger.warning(f"Anonymized PDF path returned but file does not exist: {anonymized_path}")'''




    def _finalize_processing(self):
        """Finalize processing and update state."""
        if not self.current_pdf:
            logger.warning("Cannot finalize processing - no PDF instance available")
            return
            
        try:
            # Update state based on processing results
            state = self._ensure_state(self.current_pdf)
            
            if self.processing_context.get('text_extracted') and state:
                state.mark_anonymized()
            
            # Save all changes
            with transaction.atomic():
                self.current_pdf.save()
                if state:
                    state.save()
            
            logger.info("PDF processing completed successfully")
        except Exception as e:
            logger.warning(f"Failed to finalize processing: {e}")

    def _mark_processing_incomplete(self, reason: str):
        """Mark processing as incomplete with reason."""
        if not self.current_pdf:
            logger.warning(f"Cannot mark processing incomplete - no PDF instance available. Reason: {reason}")
            return
            
        try:
            state = self._ensure_state(self.current_pdf)
            if state:
                state.text_meta_extracted = False
                state.pdf_meta_extracted = False
                state.sensitive_meta_processed = False
                state.save()
                logger.info(f"Set PDF state: processed=False due to {reason}")
            
            # Save changes
            with transaction.atomic():
                self.current_pdf.save()
        except Exception as e:
            logger.warning(f"Failed to mark processing incomplete: {e}")

    def _retry_existing_pdf(self, existing_pdf):
        """Retry processing for existing PDF."""
        try:
            # Remove from processed files to allow retry
            file_path_str = str(existing_pdf.file.path) if existing_pdf.file else None
            if file_path_str and file_path_str in self.processed_files:
                self.processed_files.remove(file_path_str)
                logger.debug(f"Removed {file_path_str} from processed files for retry")
            
            return self.import_and_anonymize(
                file_path=existing_pdf.file.path,
                center_name=existing_pdf.center.name if existing_pdf.center else "unknown_center",
                delete_source=False,
                retry=True
            )
        except Exception as e:
            logger.error(f"Failed to re-import existing PDF {existing_pdf.pdf_hash}: {e}")
            self.current_pdf = existing_pdf
            return existing_pdf

    def _cleanup_on_error(self):
        """Cleanup processing context on error."""
        try:
            if self.current_pdf and hasattr(self.current_pdf, 'state'):
                state = self._ensure_state(self.current_pdf)
                if state and self.processing_context.get('processing_started'):
                    state.text_meta_extracted = False
                    state.pdf_meta_extracted = False
                    state.sensitive_meta_processed = False
                    state.save()
                    logger.debug("Updated PDF state to indicate processing failure")
        except Exception as e:
            logger.warning(f"Error during cleanup: {e}")
        finally:
            # Always clean up processed files set to prevent blocks
            file_path = self.processing_context.get('file_path')
            if file_path and str(file_path) in self.processed_files:
                self.processed_files.remove(str(file_path))
                logger.debug(f"Removed {file_path} from processed files during error cleanup")

    def _cleanup_processing_context(self):
        """Cleanup processing context."""
        try:
            # Clean up temporary directories
            if self.processing_context.get('text_extracted'):
                crops_dir = PDF_DIR / 'cropped_regions'
                if crops_dir.exists() and not any(crops_dir.iterdir()):
                    crops_dir.rmdir()
                    
            # Always remove from processed files set after processing attempt
            file_path = self.processing_context.get('file_path')
            if file_path and str(file_path) in self.processed_files:
                self.processed_files.remove(str(file_path))
                logger.debug(f"Removed {file_path} from processed files set")
                
        except Exception as e:
            logger.warning(f"Error during context cleanup: {e}")
        finally:
            # Reset context
            self.current_pdf = None
            self.processing_context = {}

    def import_simple(
        self, 
        file_path: Union[Path, str], 
        center_name: str,
        delete_source: bool = False
    ) -> "RawPdfFile":
        """
        Simple PDF import without text processing or anonymization.
        Uses centralized PDF instance management pattern.
        
        Args:
            file_path: Path to the PDF file to import
            center_name: Name of the center to associate with PDF
            delete_source: Whether to delete the source file after import
            
        Returns:
            RawPdfFile instance after basic import
        """
        try:
            # Initialize simple processing context
            self._initialize_processing_context(file_path, center_name, delete_source, False)
            
            # Validate file
            self._validate_and_prepare_file()
            
            # Create PDF instance
            logger.info("Starting simple import - creating RawPdfFile instance...")
            self.current_pdf = RawPdfFile.create_from_file_initialized(
                file_path=self.processing_context['file_path'],
                center_name=center_name,
                delete_source=delete_source,
            )
            
            if not self.current_pdf:
                raise RuntimeError("Failed to create RawPdfFile instance")
            
            # Mark as processed
            self.processed_files.add(str(self.processing_context['file_path']))
            
            # Set basic state for simple import
            state = self._ensure_state(self.current_pdf)
            if state:
                state.text_meta_extracted = False
                state.pdf_meta_extracted = False
                state.sensitive_meta_processed = False
                state.save()
                logger.info("Set PDF state: processed=False for simple import")
            
            # Save changes
            with transaction.atomic():
                self.current_pdf.save()
            
            logger.info("Simple import completed for RawPdfFile hash: %s", self.current_pdf.pdf_hash)
            return self.current_pdf
            
        except Exception as e:
            logger.error(f"Simple PDF import failed for {file_path}: {e}")
            self._cleanup_on_error()
            raise
        finally:
            self._cleanup_processing_context()
    
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
    
    def create_sensitive_file(self, pdf_instance: "RawPdfFile" = None, file_path: Union[Path, str] = None) -> None:
        """
        Create a copy of the PDF file in the sensitive directory and update the file reference.
        Delete the source path to avoid duplicates.
        Uses the central PDF instance and processing context if parameters not provided.

        Ensures the FileField points to the file under STORAGE_DIR/pdfs/sensitive and never back to raw_pdfs.
        """
        pdf_file = pdf_instance or self.current_pdf
        source_path = Path(file_path) if file_path else self.processing_context.get('file_path')

        if not pdf_file:
            raise ValueError("No PDF instance available for creating sensitive file")
        if not source_path:
            raise ValueError("No file path available for creating sensitive file")

        SENSITIVE_DIR = PDF_DIR / "sensitive"
        target = SENSITIVE_DIR / f"{pdf_file.pdf_hash}.pdf"

        try:
            os.makedirs(SENSITIVE_DIR, exist_ok=True)

            # If source already is the target, just ensure FileField points correctly
            if source_path.resolve() == target.resolve():
                pass
            else:
                # Move the file from ingress to sensitive storage
                # Using replace semantics when target exists (re-import)
                if target.exists():
                    try:
                        target.unlink()
                    except Exception as e:
                        logger.warning("Could not remove existing sensitive target %s: %s", target, e)
                shutil.move(str(source_path), str(target))
                logger.info(f"Moved PDF to sensitive directory: {target}")

            # Update FileField to reference the file under STORAGE_DIR
            # We avoid re-saving file content (the file is already at target); set .name relative to STORAGE_DIR
            try:
                relative_name = str(target.relative_to(STORAGE_DIR)) #just point the Django FileField to the file that the anonymizer already created in data/pdfs/anonymized/.
            except ValueError:
                # Fallback: if target is not under STORAGE_DIR, store absolute path (not ideal)
                relative_name = str(target)

            # Only update when changed
            if getattr(pdf_file.file, 'name', None) != relative_name:
                pdf_file.file.name = relative_name
                pdf_file.save(update_fields=['file'])
                logger.info("Updated PDF FileField reference to sensitive path: %s", pdf_file.file.path)
            else:
                logger.debug("PDF FileField already points to sensitive path: %s", pdf_file.file.path)

            # Best-effort: if original source still exists (e.g., copy), remove it to avoid re-triggers
            try:
                if source_path.exists() and source_path != target:
                    os.remove(source_path)
                    logger.info(f"Removed original PDF file at ingress: {source_path}")
            except OSError as e:
                logger.warning(f"Could not delete original PDF file {source_path}: {e}")

        except Exception as e:
            logger.warning(f"Could not create sensitive file copy for {pdf_file.pdf_hash}: {e}", exc_info=True)

    def archive_or_quarantine_file(self, pdf_instance: "RawPdfFile" = None, source_file_path: Union[Path, str] = None, 
                                 quarantine_reason: str = None, is_pdf_problematic: bool = None) -> bool:
        """
        Archive or quarantine file based on the state of the PDF processing.
        Uses the central PDF instance and processing context if parameters not provided.
        
        Args:
            pdf_instance: Optional PDF instance, defaults to self.current_pdf
            source_file_path: Optional source file path, defaults to processing_context['file_path']
            quarantine_reason: Optional quarantine reason, defaults to processing_context['error_reason']
            is_pdf_problematic: Optional override for problematic state
            
        Returns:
            bool: True if file was quarantined, False if archived successfully
        """
        pdf_file = pdf_instance or self.current_pdf
        file_path = Path(source_file_path) if source_file_path else self.processing_context.get('file_path')
        quarantine_reason = quarantine_reason or self.processing_context.get('error_reason')
        
        if not pdf_file:
            raise ValueError("No PDF instance available for archiving/quarantine")
        if not file_path:
            raise ValueError("No file path available for archiving/quarantine")
        
        # Determine if the PDF is problematic
        pdf_problematic = is_pdf_problematic if is_pdf_problematic is not None else pdf_file.is_problematic
        
        if pdf_problematic:
            # Quarantine the file
            logger.warning(f"Quarantining problematic PDF: {pdf_file.pdf_hash}, reason: {quarantine_reason}")
            quarantine_dir = PDF_DIR / "quarantine"
            os.makedirs(quarantine_dir, exist_ok=True)
            
            quarantine_path = quarantine_dir / f"{pdf_file.pdf_hash}.pdf"
            try:
                shutil.move(file_path, quarantine_path)
                pdf_file.quarantine_reason = quarantine_reason or "File processing failed"
                pdf_file.save(update_fields=['quarantine_reason'])
                logger.info(f"Moved problematic PDF to quarantine: {quarantine_path}")
                return True
            except Exception as e:
                logger.error(f"Failed to quarantine PDF {pdf_file.pdf_hash}: {e}")
                return True  # Still consider as quarantined to prevent further processing
        else:
            # Archive the file normally
            logger.info(f"Archiving successfully processed PDF: {pdf_file.pdf_hash}")
            archive_dir = PDF_DIR / "processed"
            os.makedirs(archive_dir, exist_ok=True)
            
            archive_path = archive_dir / f"{pdf_file.pdf_hash}.pdf"
            try:
                shutil.move(file_path, archive_path)
                logger.info(f"Moved processed PDF to archive: {archive_path}")
                return False
            except Exception as e:
                logger.error(f"Failed to archive PDF {pdf_file.pdf_hash}: {e}")
                return False
