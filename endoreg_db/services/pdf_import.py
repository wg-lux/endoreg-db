"""
PDF import service module.

Provides high-level functions for importing and anonymizing PDF files,
combining RawPdfFile creation with text extraction and anonymization using lx anonymizer.

All Fields should be overwritten from anonymizer defaults except for the center which is given.
"""

import errno
import hashlib
import logging
import os
import shutil
import sys
import time
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Union
import subprocess
from django.db import transaction
from django.core.exceptions import ObjectDoesNotExist
import lx_anonymizer

from endoreg_db.models import SensitiveMeta
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.state.raw_pdf import RawPdfState
from endoreg_db.utils import paths as path_utils

logger = logging.getLogger(__name__)

# Treat lock files older than this as stale and reclaim them (in seconds)
STALE_LOCK_SECONDS = 600

if TYPE_CHECKING:
    pass  # RawPdfFile already imported above


class PdfImportService:
    """
    Service class for importing and processing PDF files with text extraction and anonymization.
    Uses a central PDF instance pattern for cleaner state management.

    Supports two processing modes:
    - 'blackening': Simple PDF masking with black rectangles over sensitive areas
    - 'cropping': Advanced mode that crops sensitive regions to separate images
    """

    def __init__(
        self, allow_meta_overwrite: bool = True, processing_mode: str = "blackening"
    ):
        """
        Create a PdfImportService configured for importing and anonymizing PDFs.
        
        Parameters:
            allow_meta_overwrite (bool): If True, existing SensitiveMeta fields may be overwritten when new metadata is extracted.
            processing_mode (str): Processing mode to use; either "blackening" for masking or "cropping" for advanced cropping. Raises ValueError for invalid modes.
        """
        self.processed_files = set()
        self._report_reader_available = None
        self._report_reader_class = None
        self.allow_meta_overwrite = allow_meta_overwrite

        # Validate and set processing mode
        valid_modes = ["blackening", "cropping"]
        if processing_mode not in valid_modes:
            raise ValueError(
                f"Invalid processing_mode '{processing_mode}'. Must be one of: {valid_modes}"
            )
        self.processing_mode = processing_mode

        # Central PDF instance management
        self.current_pdf = None
        self.current_pdf_state = None
        self.processing_context = {}
        self.original_path = None
        
        self.DEFAULT_PATIENT_FIRST_NAME = "Patient"
        self.DEFAULT_PATIENT_LAST_NAME = "Unknown"
        self.DEFAULT_PATIENT_DOB = date(1990, 1, 1)
        self.DEFAULT_CENTER_NAME = "university_hospital_wuerzburg"

    @classmethod
    def with_blackening(cls, allow_meta_overwrite: bool = False) -> "PdfImportService":
        """
        Return a PdfImportService configured to run in blackening (masking) processing mode.
        
        Parameters:
            allow_meta_overwrite (bool): If True, existing SensitiveMeta fields may be overwritten.
        
        Returns:
            PdfImportService: Service instance configured for blackening mode.
        """
        return cls(
            allow_meta_overwrite=allow_meta_overwrite, processing_mode="blackening"
        )

    @classmethod
    def with_cropping(cls, allow_meta_overwrite: bool = False) -> "PdfImportService":
        """
        Create a PdfImportService configured for advanced cropping mode.

        Args:
            allow_meta_overwrite: Whether to allow overwriting existing SensitiveMeta fields

        Returns:
            PdfImportService instance configured for cropping mode
        """
        return cls(
            allow_meta_overwrite=allow_meta_overwrite, processing_mode="cropping"
        )

    @contextmanager
    def _file_lock(self, path: Path):
        """
        Create a filesystem lock for the given path to prevent concurrent processing.
        
        Creates a ".lock" sibling file, writes a short marker, and yields control while the
        lock is held. If an existing lock is older than STALE_LOCK_SECONDS it will be
        removed and replaced. The lock file is removed when the context exits; cleanup
        errors are suppressed.
        
        Raises:
            ValueError: if another worker is currently processing the file (lock exists and is not stale).
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
                            path,
                            age,
                        )
                        lock_path.unlink()
                    except Exception as e:
                        logger.warning(
                            "Failed to remove stale lock %s: %s", lock_path, e
                        )
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
        """
        Compute the SHA256 hex digest of a file's contents.
        
        Parameters:
            path (Path): Path to the file to hash.
            chunk (int): Number of bytes to read per chunk when hashing (buffer size). Default is 1MB.
        
        Returns:
            str: SHA256 hash as a lowercase hexadecimal string.
        """
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                b = f.read(chunk)
                if not b:
                    break
                h.update(b)
        return h.hexdigest()

    def _get_pdf_dir(self) -> Path | None:
        """
        Resolve the configured PDF directory to a concrete filesystem path.
        
        Attempts to convert path_utils.PDF_DIR into a pathlib.Path and returns None if the setting is missing or cannot be resolved.
        
        Returns:
        	Path | None: A Path for the configured PDF directory, or `None` if no valid directory is configured.
        """
        candidate = getattr(path_utils, "PDF_DIR", None)
        if isinstance(candidate, Path):
            return candidate
        if candidate is None:
            return None
        try:
            derived = candidate / "."
        except Exception:
            derived = None

        if derived is not None:
            try:
                return Path(derived)
            except Exception:
                return None

        try:
            return Path(str(candidate))
        except Exception:
            return None

    def _quarantine(self, source: Path) -> Path:
        """
        Move a PDF file into the module's processing quarantine directory and return the new path.
        
        Prefers an atomic rename when possible and falls back to a cross-device copy-and-remove if required. Removes a stray ".lock" file next to the original source if present.
        
        Parameters:
            source (Path): Path to the source file to quarantine.
        
        Returns:
            Path: New path of the file inside the PDF "_processing" quarantine directory.
        
        Raises:
            OSError: If the rename/copy fails for reasons other than a cross-device move.
        """
        qdir = path_utils.PDF_DIR / "_processing"
        qdir.mkdir(parents=True, exist_ok=True)
        target = qdir / source.name
        try:
            # Try atomic rename first (fastest when on same filesystem)
            source.rename(target)
        except OSError as exc:
            if exc.errno == errno.EXDEV:
                # Cross-device move, fall back to shutil.move which copies+removes
                shutil.move(str(source), str(target))
            else:
                raise
        lock_path = Path(str(source) + ".lock")
        if lock_path.exists():
            lock_path.unlink()

        return target

    def _ensure_state(self, pdf_file: "RawPdfFile"):
        """
        Ensure a RawPdfFile has an associated RawPdfState and return it.
        
        If the PDF already has a state, that state is returned. If not and the PDF exposes
        get_or_create_state(), a new state is created, assigned to pdf_file.state and
        to this service's current_pdf_state, and then returned.
        
        Parameters:
            pdf_file (RawPdfFile): PDF model instance to ensure state for.
        
        Returns:
            RawPdfState: The associated state instance.
        """
        if getattr(pdf_file, "state", None):
            return pdf_file.state
        if hasattr(pdf_file, "get_or_create_state"):
            state = pdf_file.get_or_create_state()
            pdf_file.state = state
            self.current_pdf_state = state
            assert isinstance(self.current_pdf_state, RawPdfState)
            return state


    def _ensure_report_reading_available(self):
        """
        Ensure the lx_anonymizer ReportReader class can be imported, attempting to use LX_ANONYMIZER_PATH if needed and caching the result.
        
        Returns:
            (available, ReportReader): `available` is `True` and `ReportReader` is the class when import succeeds; `False` and `None` otherwise.
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
                    logger.info(
                        "Imported lx_anonymizer.ReportReader via LX_ANONYMIZER_PATH"
                    )
                    self._report_reader_available = True
                    self._report_reader_class = ReportReader
                    return True, ReportReader
                except Exception as e:
                    logger.warning(
                        "Failed importing lx_anonymizer via LX_ANONYMIZER_PATH: %s", e
                    )
                finally:
                    # Keep path for future imports if it worked; otherwise remove.
                    if "ReportReader" not in locals() and extra in sys.path:
                        sys.path.remove(extra)

        self._report_reader_available = False
        self._report_reader_class = None
        return False, None

    def _ensure_default_patient_data(self, pdf_instance: "RawPdfFile") -> None:
        """
        Ensure the PDF has a SensitiveMeta populated with minimum patient data.
        
        If the PDF has no SensitiveMeta, create and attach a default SensitiveMeta containing
        patient_first_name, patient_last_name, patient_dob, examination_date (set to today),
        and center_name.
        
        Parameters:
            pdf_instance (RawPdfFile | None): PDF instance to update; if falsy, uses
                self.current_pdf.
        """
        pdf_file = pdf_instance or self.current_pdf
        if not pdf_file:
            logger.warning(
                "No PDF instance available for ensuring default patient data"
            )
            return

        if not pdf_file.sensitive_meta:
            logger.info(
                f"No SensitiveMeta found for PDF {pdf_file.pdf_hash}, creating default"
            )

            # Create default SensitiveMeta with placeholder data
            default_data = {
                "patient_first_name": self.DEFAULT_PATIENT_FIRST_NAME,
                "patient_last_name": self.DEFAULT_PATIENT_LAST_NAME,
                "patient_dob": self.DEFAULT_PATIENT_DOB,
                "examination_date": date.today(),  # today is intentionally *not* a constant
                "center_name": (
                    pdf_file.center.name
                    if pdf_file.center
                    else self.DEFAULT_CENTER_NAME
                ),
            }


            try:
                sensitive_meta = SensitiveMeta.create_from_dict(default_data)
                pdf_file.sensitive_meta = sensitive_meta
                pdf_file.save(update_fields=["sensitive_meta"])
                logger.info(
                    f"Created default SensitiveMeta for PDF {pdf_file.pdf_hash}"
                )
            except Exception as e:
                logger.error(
                    f"Failed to create default SensitiveMeta for PDF {pdf_file.pdf_hash}: {e}"
                )

    def import_and_anonymize(
        self,
        file_path: Union[Path, str],
        center_name: str,
        delete_source: bool = False,
        retry: bool = False,
    ) -> "RawPdfFile | None":
        """
        Import a PDF, extract text and metadata, anonymize it according to the service's processing mode, and return the associated RawPdfFile.
        
        Processing mode configured on the service ('blackening' or 'cropping') controls the anonymization output (masked PDF or cropped regions). The method handles creating or reusing a RawPdfFile instance, applying extracted text and metadata, attaching any generated anonymized PDF, and updating processing state.
        
        Parameters:
            file_path (Union[Path, str]): Path to the PDF file to import.
            center_name (str): Name of the center to associate with the PDF.
            delete_source (bool): If True, delete the original source file after successful import.
            retry (bool): If True, attempt to reprocess an existing RawPdfFile instance (useful when a previous import left the PDF partially processed).
        
        Returns:
            RawPdfFile | None: The persisted RawPdfFile instance after processing, or `None` if the file was skipped.
        """
        try:
            # Initialize processing context
            self._initialize_processing_context(
                file_path, center_name, delete_source, retry
            )

            # Step 1: Validate and prepare file
            self._validate_and_prepare_file()

            # Step 2: Create or retrieve PDF instance
            self._create_or_retrieve_pdf_instance()

            # Early return check - if no PDF instance was created, return None
            if not self.current_pdf:
                logger.warning(
                    f"No PDF instance created for {file_path}, returning None"
                )
                raise ObjectDoesNotExist
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
                return
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

    def _initialize_processing_context(
        self,
        file_path: Union[Path, str],
        center_name: str,
        delete_source: bool,
        retry: bool,
    ):
        """
        Prepare internal processing context for importing a PDF and mark the file as the current original path.
        
        Parameters:
            file_path (Path | str): Path to the PDF being imported.
            center_name (str): Name of the center associated with the PDF.
            delete_source (bool): Whether the original source file should be deleted after successful processing.
            retry (bool): Whether this invocation is a retry attempt for an existing PDF.
        
        Raises:
            ValueError: If the file is already being processed in the current session.
        """
        self.processing_context = {
            "file_path": Path(file_path),
            "original_file_path": Path(file_path),
            "center_name": center_name,
            "delete_source": delete_source,
            "retry": retry,
            "file_hash": None,
            "processing_started": False,
            "text_extracted": False,
            "metadata_processed": False,
            "anonymization_completed": False,
        }
        self.original_path = Path(file_path)

        # Check if already processed (only during current session to prevent race conditions)
        if str(file_path) in self.processed_files:
            logger.info(
                f"File {file_path} already being processed in current session, skipping"
            )
            raise ValueError("File already being processed")

        logger.info(f"Starting import and processing for: {file_path}")

    def _validate_and_prepare_file(self):
        """
        Ensure the configured file exists and compute its SHA256 hash.
        
        Checks that the Path stored in self.processing_context["file_path"] exists and stores its SHA256 digest in self.processing_context["file_hash"]. If hash computation fails, stores None in "file_hash". Raises FileNotFoundError when the file is missing.
        """
        file_path = self.processing_context["file_path"]

        if not file_path.exists():
            raise FileNotFoundError(f"PDF file not found: {file_path}")

        try:
            self.processing_context["file_hash"] = self._sha256(file_path)
        except Exception as e:
            logger.warning(f"Could not calculate file hash: {e}")
            self.processing_context["file_hash"] = None

    def _create_or_retrieve_pdf_instance(self):
        """
        Ensure self.current_pdf is set to a RawPdfFile corresponding to the current processing context, creating a new database instance when necessary or reusing an existing one.
        
        This will:
        - Use a file lock (when not retrying) to avoid duplicate creation.
        - If a matching PDF already exists and has extracted text, use it and return early.
        - If a matching PDF exists but has not been processed, attempt a reprocess path.
        - On retry, retrieve the existing RawPdfFile by pdf_hash instead of creating a new one.
        - Handle race conditions by falling back to an existing instance when an IntegrityError occurs.
        
        Raises:
            RuntimeError: If creation completes without producing a RawPdfFile instance.
        """
        file_path = self.processing_context["file_path"]
        center_name = self.processing_context["center_name"]
        delete_source = self.processing_context["delete_source"]
        retry = self.processing_context["retry"]
        file_hash = self.processing_context["file_hash"]

        if not retry:
            # Check for existing PDF and handle duplicates
            with self._file_lock(file_path):
                existing = None
                if file_hash and RawPdfFile.objects.filter(pdf_hash=file_hash).exists():
                    existing = RawPdfFile.objects.get(pdf_hash=file_hash)

                if existing:
                    logger.info(f"Found existing RawPdfFile {existing.pdf_hash}")
                    if existing.text:
                        logger.info(
                            f"Existing PDF {existing.pdf_hash} already processed - returning"
                        )
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
                logger.info(
                    f"Retrying import for existing RawPdfFile {self.current_pdf.pdf_hash}"
                )

                # Check if retry is actually needed
                if self.current_pdf.text:
                    logger.info(
                        f"Existing PDF {self.current_pdf.pdf_hash} already processed during retry - returning"
                    )
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
        """
        Prepare the processing environment for the current PDF and initialize its processing state.
        
        Creates a sensitive copy of the PDF (if an original path is available), updates processing_context keys
        ("file_path", "sensitive_copy_created", "sensitive_file_path", "processing_started"), ensures a RawPdfState
        exists and is marked as processing started, records the file in processed_files to prevent duplicates,
        and ensures default patient SensitiveMeta is present on the PDF.
        
        Raises:
            RuntimeError: If no current PDF can be resolved from the known file hash.
        """
        original_path = self.processing_context.get("file_path")
        if not original_path or not self.current_pdf:
            try:
                self.current_pdf = RawPdfFile.objects.get(pdf_hash=self.processing_context["file_hash"])
                self.original_path = Path(str(self.current_pdf.file.path))
                    
            except RawPdfFile.DoesNotExist:
                raise RuntimeError("Processing environment setup failed")
        # Create sensitive file copy
        if original_path is None or not isinstance(original_path, (str, Path)):
            logger.error(f"No original path: {original_path!r}")
            return
        self.create_sensitive_file(self.current_pdf, original_path)

        # Update file path to point to sensitive copy
        self.processing_context["file_path"] = self.current_pdf.file.path
        self.processing_context["sensitive_copy_created"] = True
        try:
            self.processing_context["sensitive_file_path"] = Path(
                self.current_pdf.file.path
            )
        except Exception:
            self.processing_context["sensitive_file_path"] = None

        # Ensure state exists
        state = self.current_pdf.get_or_create_state()
        state.mark_processing_started()
        self.processing_context["processing_started"] = True

        # Mark as processed to prevent duplicates
        self.processed_files.add(str(self.processing_context["file_path"]))

        # Ensure default patient data
        logger.info("Ensuring default patient data...")
        self._ensure_default_patient_data(self.current_pdf)

    def _process_text_and_metadata(self):
        """
        Orchestrates text extraction and metadata processing for the current PDF using the ReportReader.
        
        This ensures a ReportReader implementation is available, instantiates it with storage and locale settings,
        and delegates work to the mode-specific handlers (_process_with_cropping or _process_with_blackening).
        On missing ReportReader or missing current PDF/file the function marks the processing as incomplete with
        an appropriate reason. On processing exceptions it marks the processing incomplete with reason "text_processing_failed".
        """
        report_reading_available, ReportReaderCls = self._ensure_report_reading_available()
        try:
            assert ReportReaderCls is not None and report_reading_available
            assert self.current_pdf is not None 
        except AssertionError as e:
            logger.error(f"PDF Import failed on Error:{e} Ensure the pdf was passed correctly and report reading is available in function _process_text_and_metadata() ")
        if not report_reading_available:
            logger.warning("Report reading not available (lx_anonymizer not found)")
            self._mark_processing_incomplete("no_report_reader")
            return 
        assert self.current_pdf is not None
        if not self.current_pdf.file:
            logger.warning("No file available for text processing")
            self._mark_processing_incomplete("no_file")
            return

        try:
            logger.info(
                f"Starting text extraction and metadata processing with ReportReader (mode: {self.processing_mode})..."
            )
            ReportReaderCls = lx_anonymizer.ReportReader            

            # Initialize ReportReader
            report_reader = ReportReaderCls(
                report_root_path=str(path_utils.STORAGE_DIR),
                locale="de_DE",
                text_date_format="%d.%m.%Y",
            )

            if self.processing_mode == "cropping":
                # Use advanced cropping method (existing implementation)
                self._process_with_cropping(report_reader)
            else:  # blackening mode
                # Use enhanced process_report with PDF masking
                self._process_with_blackening(report_reader)

        except Exception as e:
            logger.warning(f"Text processing failed: {e}")
            self._mark_processing_incomplete("text_processing_failed")

    def _process_with_blackening(self, report_reader):
        """
        Run the report reader in blackening mode to extract text and metadata and produce an anonymized PDF, then apply and record the results on the current PDF.
        
        This method invokes the provided report_reader to obtain original text, anonymized text, extracted metadata, and a path to a pre-generated anonymized PDF. It stores these results in the service's processing_context, applies text and metadata to the current PDF, attaches the anonymized PDF to the PDF record, and sets processing flags indicating which steps completed.
        
        Parameters:
            report_reader: An object implementing the report reading interface (expected to provide a `process_report` method) used to process the current PDF.
        """
        logger.info("Using simple PDF blackening mode...")

        # Setup anonymized directory
        anonymized_dir = path_utils.PDF_DIR / "anonymized"
        anonymized_dir.mkdir(parents=True, exist_ok=True)
        assert self.current_pdf is not None
        # Generate output path for anonymized PDF
        pdf_hash = self.current_pdf.pdf_hash
        anonymized_output_path = anonymized_dir / f"{pdf_hash}_anonymized.pdf"

        # Process with enhanced process_report method (returns 4-tuple now)
        original_text, anonymized_text, extracted_metadata, anonymized_pdf_path = (
            report_reader.process_report(
                pdf_path=self.processing_context["file_path"],
                create_anonymized_pdf=True,
                anonymized_pdf_output_path=str(anonymized_output_path),
            )
        )

        # Store results in context
        self.processing_context.update(
            {
                "original_text": original_text,
                "anonymized_text": anonymized_text,
                "extracted_metadata": extracted_metadata,
                "cropped_regions": None,  # Not available in blackening mode
                "anonymized_pdf_path": anonymized_pdf_path,
            }
        )

        # Apply results
        if original_text:
            self._apply_text_results()
            self.processing_context["text_extracted"] = True

        if extracted_metadata:
            self._apply_metadata_results()
            self.processing_context["metadata_processed"] = True

        if anonymized_pdf_path:
            self._apply_anonymized_pdf()
            self.processing_context["anonymization_completed"] = True

        logger.info("PDF blackening processing completed")

    def _process_with_cropping(self, report_reader):
        """
        Run the advanced cropping workflow for the current PDF and persist results into the service state.
        
        Invokes the provided report_reader to perform cropping-based processing for the PDF located at processing_context["file_path"]. Creates required output directories under PDF_DIR ("cropped_regions" and "anonymized"), stores the returned original text, anonymized text, extracted metadata, cropped region descriptors, and anonymized PDF path into processing_context, and applies results to the current PDF state. When present, extracted text, metadata, and anonymized PDF are applied via the service's _apply_text_results, _apply_metadata_results, and _apply_anonymized_pdf helpers and corresponding processing_context flags ("text_extracted", "metadata_processed", "anonymization_completed") are set.
        
        Parameters:
            report_reader: An object that exposes process_report_with_cropping(pdf_path, crop_sensitive_regions, crop_output_dir, anonymization_output_dir) and returns a tuple (original_text, anonymized_text, extracted_metadata, cropped_regions, anonymized_pdf_path).
        
        """
        logger.info("Using advanced cropping mode...")

        # Setup output directories
        crops_dir = path_utils.PDF_DIR / "cropped_regions"
        anonymized_dir = path_utils.PDF_DIR / "anonymized"
        crops_dir.mkdir(parents=True, exist_ok=True)
        anonymized_dir.mkdir(parents=True, exist_ok=True)

        # Process with cropping (returns 5-tuple)
        (
            original_text,
            anonymized_text,
            extracted_metadata,
            cropped_regions,
            anonymized_pdf_path,
        ) = report_reader.process_report_with_cropping(
            pdf_path=self.processing_context["file_path"],
            crop_sensitive_regions=True,
            crop_output_dir=str(crops_dir),
            anonymization_output_dir=str(anonymized_dir),
        )

        # Store results in context
        self.processing_context.update(
            {
                "original_text": original_text,
                "anonymized_text": anonymized_text,
                "extracted_metadata": extracted_metadata,
                "cropped_regions": cropped_regions,
                "anonymized_pdf_path": anonymized_pdf_path,
            }
        )

        # Apply results
        if original_text:
            self._apply_text_results()
            self.processing_context["text_extracted"] = True

        if extracted_metadata:
            self._apply_metadata_results()
            self.processing_context["metadata_processed"] = True

        if anonymized_pdf_path:
            self._apply_anonymized_pdf()
            self.processing_context["anonymization_completed"] = True

        logger.info("PDF cropping processing completed")

    def _apply_text_results(self):
        """
        Apply extracted text results from the processing context to the current PDF instance.
        
        Assigns the value of "original_text" from processing_context to self.current_pdf.text. If "anonymized_text" is present and differs from the original text, marks the PDF as anonymized by setting self.current_pdf.anonymized = True. If there is no current PDF or no original text in the context, the method makes no changes.
        """
        if not self.current_pdf:
            logger.warning("Cannot apply text results - no PDF instance available")
            return

        original_text = self.processing_context.get("original_text")
        anonymized_text = self.processing_context.get("anonymized_text")

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
        """
        Apply extracted metadata to the current PDF's SensitiveMeta fields.
        
        Reads `extracted_metadata` from the processing context and maps known extraction keys to
        SensitiveMeta attributes. Date-like fields are parsed with `_parse_date_field`; string
        placeholders (where the extracted value equals the metadata key) are ignored. Fields are
        updated only if the service is configured to allow metadata overwrites (`allow_meta_overwrite`)
        or if the existing value is considered a placeholder via `_is_placeholder_value`. If any
        fields are changed the SensitiveMeta is saved and the updated field names are logged.
        
        Early exits occur when there is no current PDF, no SensitiveMeta on the PDF, or no extracted
        metadata available.
        """
        if not self.current_pdf:
            logger.warning("Cannot apply metadata results - no PDF instance available")
            return

        extracted_metadata = self.processing_context.get("extracted_metadata")

        if not self.current_pdf.sensitive_meta or not extracted_metadata:
            logger.debug("No sensitive meta or extracted metadata available")
            return

        sm = self.current_pdf.sensitive_meta

        # Map ReportReader metadata to SensitiveMeta fields
        metadata_mapping = {
            "patient_first_name": "patient_first_name",
            "patient_last_name": "patient_last_name",
            "patient_dob": "patient_dob",
            "examination_date": "examination_date",
            "examiner_first_name": "examiner_first_name",
            "examiner_last_name": "examiner_last_name",
            "endoscope_type": "endoscope_type",
            "casenumber": "casenumber",
            "center_name": "center_name",
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
                if sm_field in ["patient_dob", "examination_date"]:
                    new_value = self._parse_date_field(raw_value, meta_key, sm_field)
                    if new_value is None:
                        continue
                else:
                    new_value = raw_value

                # Configurable overwrite policy
                should_overwrite = (
                    self.allow_meta_overwrite
                    or self._is_placeholder_value(sm_field, old_value)
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
                        sm_field,
                        raw_value,
                    )
                    return None

                # Try common date formats
                date_formats = ["%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y"]
                for fmt in date_formats:
                    try:
                        return datetime.strptime(raw_value, fmt).date()
                    except ValueError:
                        continue

                logger.warning(
                    "Could not parse date '%s' for field %s", raw_value, sm_field
                )
                return None

            elif hasattr(raw_value, "date"):
                return raw_value.date()
            else:
                return raw_value

        except (ValueError, AttributeError) as e:
            logger.warning("Date parsing failed for %s: %s", sm_field, e)
            return None

    # from gc-08
    def _apply_anonymized_pdf(self):
        """
        Attach a pre-generated anonymized PDF to the current RawPdfFile and mark the PDF as anonymized.
        
        Sets the PDF instance's FileField to point at the anonymized file (preferring a path relative to STORAGE_DIR when possible), ensures the instance's anonymized flag and processing state reflect completion, and saves those changes. If there is no current PDF, no anonymized path in the processing context, or the target file does not exist, the function returns without making changes.
        """
        if not self.current_pdf:
            logger.warning("Cannot apply anonymized PDF - no PDF instance available")
            return

        anonymized_pdf_path = self.processing_context.get("anonymized_pdf_path")
        if not anonymized_pdf_path:
            logger.debug("No anonymized_pdf_path present in processing context")
            return

        anonymized_path = Path(anonymized_pdf_path)
        if not anonymized_path.exists():
            logger.warning(
                "Anonymized PDF path returned but file does not exist: %s",
                anonymized_path,
            )
            return

        logger.info("Anonymized PDF created by ReportReader at: %s", anonymized_path)

        try:
            # Prefer storing a path relative to STORAGE_DIR so Django serves it correctly
            try:
                relative_name = str(anonymized_path.relative_to(path_utils.STORAGE_DIR))
            except ValueError:
                # Fallback to absolute path if the file lives outside STORAGE_DIR
                relative_name = str(anonymized_path)

            # Only update if something actually changed
            if getattr(self.current_pdf.anonymized_file, "name", None) != relative_name:
                self.current_pdf.anonymized_file.name = relative_name

            # Ensure model/state reflect anonymization even if text didn't differ
            if not getattr(self.current_pdf, "anonymized", False):
                self.current_pdf.anonymized = True

            # Persist cropped regions info somewhere useful (optional & non-breaking)
            # If your model has a field for this, persist there; otherwise we just log.
            cropped_regions = self.processing_context.get("cropped_regions")
            if cropped_regions:
                logger.debug(
                    "Cropped regions recorded (%d regions).", len(cropped_regions)
                )

            # Save model changes
            update_fields = ["anonymized_file"]
            if "anonymized" in self.current_pdf.__dict__:
                update_fields.append("anonymized")
            self.current_pdf.save(update_fields=update_fields)

            # Mark state as anonymized immediately; this keeps downstream flows working
            state = self._ensure_state(self.current_pdf)
            
            if state and not state.processing_started:
                state.mark_processing_started()

            logger.info(
                "Updated anonymized_file reference to: %s",
                self.current_pdf.anonymized_file.name,
            )

        except Exception as e:
            logger.warning("Could not set anonymized file reference: %s", e)

    def _finalize_processing(self):
        """
        Finalize the current PDF processing run by updating related state and persisting changes.
        
        Updates the associated RawPdfState based on processing_context flags (e.g., mark anonymized when text was extracted, mark sensitive metadata processed when anonymization completed), saves the current PDF and its state inside a database transaction, and emits informational logs about completion or failure.
        """
        if not self.current_pdf:
            logger.warning("Cannot finalize processing - no PDF instance available")
            return

        try:
            # Update state based on processing results
            state = self._ensure_state(self.current_pdf)

            if self.processing_context.get("text_extracted") and state:
                state.mark_anonymized()

            # Mark as ready for validation after successful anonymization
            if self.processing_context.get("anonymization_completed") and state:
                state.mark_sensitive_meta_processed()
                logger.info(
                    f"PDF {self.current_pdf.pdf_hash} processing completed - "
                    f"ready for validation (status: {state.anonymization_status})"
                )

            # Save all changes
            with transaction.atomic():
                self.current_pdf.save()
                if state:
                    state.save()

            logger.info("PDF processing completed successfully")
        except Exception as e:
            logger.warning(f"Failed to finalize processing: {e}")

    def _mark_processing_incomplete(self, reason: str):
        """
        Mark the current PDF's processing state as incomplete.
        
        Resets processing-related flags on the associated RawPdfState (text_meta_extracted,
        pdf_meta_extracted, sensitive_meta_processed) and persists the state and the current
        PDF object. If no current PDF is set, a warning is logged and no action is taken.
        Any failures during persistence are logged.
        
        Parameters:
            reason (str): Human-readable explanation for why processing is being marked incomplete;
                included in log messages.
        """
        if not self.current_pdf:
            logger.warning(
                f"Cannot mark processing incomplete - no PDF instance available. Reason: {reason}"
            )
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
        """
        Attempt to retry processing for an existing PDF using its original raw file path.
        
        If the original raw file is located via get_raw_file_path() and exists, removes that path from the in-memory processed set (to allow reprocessing) and invokes import_and_anonymize with retry=True using the raw file. If the raw file is missing or an error occurs, sets self.current_pdf to the provided existing_pdf and returns it unchanged.
        
        Returns:
            RawPdfFile: The RawPdfFile instance that will be (or was) processed — either the re-imported PDF result from import_and_anonymize or the original existing_pdf if retry could not proceed.
        """
        try:
            # ✅ FIX: Use get_raw_file_path() to find original file
            raw_file_path = existing_pdf.get_raw_file_path()

            if not raw_file_path or not raw_file_path.exists():
                logger.error(
                    f"Cannot retry PDF {existing_pdf.pdf_hash}: Raw file not found. "
                    f"Please re-upload the original PDF file."
                )
                self.current_pdf = existing_pdf
                return existing_pdf

            logger.info(f"Found raw file for retry at: {raw_file_path}")

            # Remove from processed files to allow retry
            file_path_str = str(raw_file_path)
            if file_path_str in self.processed_files:
                self.processed_files.remove(file_path_str)
                logger.debug(f"Removed {file_path_str} from processed files for retry")

            return self.import_and_anonymize(
                file_path=raw_file_path,  # ✅ Use raw file path, not sensitive path
                center_name=existing_pdf.center.name
                if existing_pdf.center
                else "unknown_center",
                delete_source=False,  # Never delete during retry
                retry=True,
            )
        except Exception as e:
            logger.error(
                f"Failed to re-import existing PDF {existing_pdf.pdf_hash}: {e}"
            )
            self.current_pdf = existing_pdf
            return existing_pdf

    def _cleanup_on_error(self):
        """
        Perform cleanup after a failed PDF processing run and attempt to restore a clean state for future retries.
        
        If a PDF instance and its state are available, restore the original raw file for reprocessing when possible, remove stale lock files, and reset processing-related state flags to indicate processing did not complete. Remove any sensitive copy that was created during this run, delete stray PDF files created under the configured PDF directories (including sensitive, anonymized, cropped_regions, and _processing subdirectories), and remove empty subdirectories where appropriate. Also ensure the in-memory processed_files tracking is cleared for the current file and log a summary of remaining file counts for diagnostic purposes.
        """
        original_path = self.original_path
        try:
            if self.current_pdf and hasattr(self.current_pdf, "state"):
                state = self._ensure_state(self.current_pdf)
                raw_file_path = self.current_pdf.get_raw_file_path()
                if raw_file_path is not None and original_path is not None:
                    # Ensure reprocessing for next attempt by restoring original file
                    shutil.copy2(str(raw_file_path), str(original_path))
                    
                # Ensure no two files can remain
                if raw_file_path == original_path and raw_file_path is not None and original_path is not None:
                    os.remove(str(raw_file_path))
                    
                    
                # Remove Lock file also
                lock_path = Path(str(path_utils.PDF_DIR) + ".lock")
                try:
                    if lock_path.exists():
                        lock_path.unlink()
                        logger.info("Removed lock file during quarantine: %s", lock_path)
                except Exception as e:
                    logger.warning("Could not remove lock file during quarantine: %s", e)

                
                if state and self.processing_context.get("processing_started"):
                    state.text_meta_extracted = False
                    state.pdf_meta_extracted = False
                    state.sensitive_meta_processed = False
                    state.anonymized = False
                    state.save()
                    logger.debug("Updated PDF state to indicate processing failure")
            else:
                # 🔧 Early failure: no current_pdf (or no state).
                # In this case we want to make sure we don't leave stray files
                # under PDF_DIR or PDF_DIR/sensitive.

                pdf_dir = self._get_pdf_dir()
                if pdf_dir and pdf_dir.exists():
                    for candidate_dir in (pdf_dir, pdf_dir / "sensitive"):
                        if candidate_dir.exists():
                            for candidate in candidate_dir.glob("*.pdf"):
                                # Don't delete the original ingress file
                                if (
                                    original_path is not None
                                    and candidate.resolve() == Path(original_path).resolve()
                                ):
                                    continue
                                try:
                                    candidate.unlink()
                                    logger.debug(
                                        "Removed stray PDF during early error cleanup: %s",
                                        candidate,
                                    )
                                except Exception as e:
                                    logger.warning(
                                        "Failed to remove stray PDF %s: %s",
                                        candidate,
                                        e,
                                    )

        except Exception as e:
            logger.warning(f"Error during cleanup: {e}")
        finally:
            # Remove any sensitive copy created during this processing run
            sensitive_created = self.processing_context.get("sensitive_copy_created")
            if sensitive_created:
                pdf_obj = self.current_pdf
                try:
                    if pdf_obj:
                        file_field = getattr(pdf_obj, "file", None)
                        if file_field and getattr(file_field, "name", None):
                            storage_name = file_field.name
                            file_field.delete(save=False)
                            logger.debug(
                                "Deleted sensitive copy %s during error cleanup",
                                storage_name,
                            )
                except Exception as cleanup_exc:
                    logger.warning(
                        "Failed to remove sensitive copy during error cleanup: %s",
                        cleanup_exc,
                    )
                pdf_dir = self._get_pdf_dir()
                if original_path and pdf_dir:
                    # Try to remove any extra file that was created during import
                    # Simplest heuristic: same basename as original, but in pdf dir or pdf/sensitive dir
                    for candidate_dir in (pdf_dir, pdf_dir / "sensitive"):
                        candidate = candidate_dir / original_path.name
                        if candidate.exists() and candidate != original_path:
                            try:
                                candidate.unlink()
                                logger.debug(
                                    "Removed stray PDF copy during early error cleanup: %s",
                                    candidate,
                                )
                            except Exception as e:
                                logger.warning(
                                    "Failed to remove stray PDF copy %s: %s",
                                    candidate,
                                    e,
                                )

            # Always clean up processed files set to prevent blocks
            file_path = self.processing_context.get("file_path")
            if file_path and str(file_path) in self.processed_files:
                self.processed_files.remove(str(file_path))
                logger.debug(
                    f"Removed {file_path} from processed files during error cleanup"
                )

            try:
                raw_dir = (
                    original_path.parent if isinstance(original_path, Path) else None
                )

                pdf_dir = self._get_pdf_dir()
                if not pdf_dir and raw_dir:
                    base_dir = raw_dir.parent
                    dir_name = getattr(path_utils, "PDF_DIR_NAME", "pdfs")
                    fallback_pdf_dir = base_dir / dir_name
                    logger.debug(
                        "PDF cleanup fallback resolution - base: %s, dir_name: %s, exists: %s",
                        base_dir,
                        dir_name,
                        fallback_pdf_dir.exists(),
                    )
                    if fallback_pdf_dir.exists():
                        pdf_dir = fallback_pdf_dir

                # Remove empty PDF subdirectories that might have been created during setup
                if pdf_dir and pdf_dir.exists():
                    for subdir_name in (
                        "sensitive",
                        "cropped_regions",
                        "anonymized",
                        "_processing",
                    ):
                        subdir_path = pdf_dir / subdir_name
                        if subdir_path.exists() and subdir_path.is_dir():
                            try:
                                next(subdir_path.iterdir())
                            except StopIteration:
                                try:
                                    subdir_path.rmdir()
                                    logger.debug(
                                        "Removed empty directory %s during error cleanup",
                                        subdir_path,
                                    )
                                except OSError as rm_err:
                                    logger.debug(
                                        "Could not remove directory %s: %s",
                                        subdir_path,
                                        rm_err,
                                    )
                            except Exception as iter_err:
                                logger.debug(
                                    "Could not inspect directory %s: %s",
                                    subdir_path,
                                    iter_err,
                                )

                raw_count = (
                    len(list(raw_dir.glob("*")))
                    if raw_dir and raw_dir.exists()
                    else None
                )
                pdf_count = (
                    len(list(pdf_dir.glob("*")))
                    if pdf_dir and pdf_dir.exists()
                    else None
                )

                sensitive_path = self.processing_context.get("sensitive_file_path")
                if sensitive_path:
                    sensitive_parent = Path(sensitive_path).parent
                    sensitive_count = (
                        len(list(sensitive_parent.glob("*")))
                        if sensitive_parent.exists()
                        else None
                    )
                else:
                    sensitive_dir = pdf_dir / "sensitive" if pdf_dir else None
                    sensitive_count = (
                        len(list(sensitive_dir.glob("*")))
                        if sensitive_dir and sensitive_dir.exists()
                        else None
                    )

                logger.info(
                    "PDF import error cleanup counts - raw: %s, pdf: %s, sensitive: %s",
                    raw_count,
                    pdf_count,
                    sensitive_count,
                )
            except Exception:
                pass

    def _cleanup_processing_context(self):
        """
        Clean up processing context and reset internal state after a processing attempt.
        
        If text was extracted, attempts to remove an empty cropped regions directory. Always removes the current processing file path from the in-memory processed_files set (if present). Any cleanup errors are logged and do not propagate. Finally, clears the current PDF reference and resets the processing_context dictionary.
        """
        try:
            # Clean up temporary directories
            if self.processing_context.get("text_extracted"):
                crops_dir = path_utils.PDF_DIR / "cropped_regions"
                if crops_dir.exists() and not any(crops_dir.iterdir()):
                    crops_dir.rmdir()

            # Always remove from processed files set after processing attempt
            file_path = self.processing_context.get("file_path")
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
        self, file_path: Union[Path, str], center_name: str, delete_source: bool = False
    ) -> "RawPdfFile":
        """
        Import a PDF file into the system without performing text extraction or anonymization.
        
        Parameters:
            file_path (Union[Path, str]): Path to the PDF file to import.
            center_name (str): Name of the center to associate with the created PDF record.
            delete_source (bool): If True, delete the source file after a successful import.
        
        Returns:
            RawPdfFile: The created RawPdfFile instance representing the imported PDF.
        """
        try:
            # Initialize simple processing context
            self._initialize_processing_context(
                file_path, center_name, delete_source, False
            )

            # Validate file
            self._validate_and_prepare_file()

            # Create PDF instance
            logger.info("Starting simple import - creating RawPdfFile instance...")
            self.current_pdf = RawPdfFile.create_from_file_initialized(
                file_path=self.processing_context["file_path"],
                center_name=center_name,
                delete_source=delete_source,
            )

            if not self.current_pdf:
                raise RuntimeError("Failed to create RawPdfFile instance")

            # Mark as processed
            self.processed_files.add(str(self.processing_context["file_path"]))

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

            logger.info(
                "Simple import completed for RawPdfFile hash: %s",
                self.current_pdf.pdf_hash,
            )
            return self.current_pdf

        except Exception as e:
            logger.error(f"Simple PDF import failed for {file_path}: {e}")
            self._cleanup_on_error()
            raise
        finally:
            self._cleanup_processing_context()

    def check_storage_capacity(
        self, file_path: Union[Path, str], storage_root, min_required_space
    ) -> bool:
        """
        Determine whether there is enough free space under storage_root to store the given PDF file.
        
        Parameters:
            file_path (Path | str): Path to the local PDF file to check.
            storage_root (str | Path): Filesystem root or directory whose available space will be checked.
            min_required_space (int | Any): Optional minimum required free bytes; currently not used by the check.
        
        Returns:
            True if the file size is less than or equal to the available free space.
        
        Raises:
            FileNotFoundError: If file_path does not exist.
            endoreg_db.exceptions.InsufficientStorageError: If there is not enough free space to store the file.
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
            raise InsufficientStorageError(
                f"Not enough space to store PDF file: {file_path}"
            )
        logger.info(
            f"Storage check passed for {file_path}: {file_size} bytes, {free} bytes available"
        )

        return True

    def create_sensitive_file(
        self, pdf_instance: "RawPdfFile", file_path: Union[Path, str]
    ) -> None:
        """
        Create a sensitive copy of the given PDF and update the PDF model to reference it.
        
        Creates or moves the file into the sensitive storage directory (PDF_DIR / "sensitive") using the PDF's pdf_hash as the filename, updates the PDF instance's FileField to point to the path relative to STORAGE_DIR when possible, and removes the original ingress file to avoid duplicate processing. If the sensitive target already exists it will be replaced; if the source already resides at the target location the FileField is still validated and updated if necessary.
        
        Parameters:
            pdf_instance (RawPdfFile): The PDF model instance to update.
            file_path (Path | str): Path to the source PDF file to copy/move into sensitive storage.
        
        Raises:
            ValueError: If no PDF instance is provided or no source file path can be determined.
        """
        pdf_file = pdf_instance or self.current_pdf
        source_path = (
            Path(file_path) if file_path else self.processing_context.get("file_path")
        )

        if not pdf_file:
            raise ValueError("No PDF instance available for creating sensitive file")
        if not source_path:
            raise ValueError("No file path available for creating sensitive file")

        SENSITIVE_DIR = path_utils.PDF_DIR / "sensitive"
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
                        logger.warning(
                            "Could not remove existing sensitive target %s: %s",
                            target,
                            e,
                        )
                shutil.move(str(source_path), str(target))
                logger.info(f"Moved PDF to sensitive directory: {target}")

            # Update FileField to reference the file under STORAGE_DIR
            # We avoid re-saving file content (the file is already at target); set .name relative to STORAGE_DIR
            try:
                relative_name = str(
                    target.relative_to(path_utils.STORAGE_DIR)
                )  # Point Django FileField to sensitive storage
            except ValueError:
                # Fallback: if target is not under STORAGE_DIR, store absolute path (not ideal)
                relative_name = str(target)

            # Only update when changed
            if getattr(pdf_file.file, "name", None) != relative_name:
                pdf_file.file.name = relative_name
                pdf_file.save(update_fields=["file"])
                logger.info(
                    "Updated PDF FileField reference to sensitive path: %s",
                    pdf_file.file.path,
                )
            else:
                logger.debug(
                    "PDF FileField already points to sensitive path: %s",
                    pdf_file.file.path,
                )

            # Best-effort: if original source still exists (e.g., copy), remove it to avoid re-triggers
            try:
                if source_path.exists() and source_path != target:
                    os.remove(source_path)
                    logger.info(f"Removed original PDF file at ingress: {source_path}")
            except OSError as e:
                logger.warning(f"Could not delete original PDF file {source_path}: {e}")

        except Exception as e:
            logger.warning(
                f"Could not create sensitive file copy for {pdf_file.pdf_hash}: {e}",
                exc_info=True,
            )

    def archive_or_quarantine_file(
        self,
        pdf_instance: "RawPdfFile",
        source_file_path: Union[Path, str],
        quarantine_reason: str,
        is_pdf_problematic: bool,
    ) -> bool:
        """
        Decide whether to quarantine or archive a PDF file and perform the chosen action.
        
        If the PDF is considered problematic, the function moves the source file to the quarantine directory and records the quarantine reason on the PDF instance; otherwise it moves the file to the processed archive directory. Raises ValueError if no PDF instance or no source file path is available.
        
        Parameters:
            pdf_instance (RawPdfFile): The PDF model instance to update; if None, uses the service's current_pdf.
            source_file_path (Path | str): Path to the source file to move; if None, uses processing_context['file_path'].
            quarantine_reason (str): Reason to record when quarantining the PDF.
            is_pdf_problematic (bool): When provided, overrides the PDF instance's problematic flag to determine action.
        
        Returns:
            bool: `True` if the file was quarantined (or treated as quarantined on failure), `False` if archived successfully.
        """
        pdf_file = pdf_instance or self.current_pdf
        file_path = (
            Path(source_file_path)
            if source_file_path
            else self.processing_context.get("file_path")
        )
        quarantine_reason = str(quarantine_reason or self.processing_context.get(
            "error_reason"
        ))

        if not pdf_file:
            raise ValueError("No PDF instance available for archiving/quarantine")
        if not file_path:
            raise ValueError("No file path available for archiving/quarantine")

        # Determine if the PDF is problematic
        pdf_problematic = (
            is_pdf_problematic
            if is_pdf_problematic is not None
            else pdf_file.is_problematic
        )

        if pdf_problematic:
            # Quarantine the file
            logger.warning(
                f"Quarantining problematic PDF: {pdf_file.pdf_hash}, reason: {quarantine_reason}"
            )
            quarantine_dir = path_utils.PDF_DIR / "quarantine"
            os.makedirs(quarantine_dir, exist_ok=True)

            quarantine_path = quarantine_dir / f"{pdf_file.pdf_hash}.pdf"
            try:
                shutil.move(file_path, quarantine_path)
                pdf_file.save(update_fields=["quarantine_reason"])
                logger.info(f"Moved problematic PDF to quarantine: {quarantine_path}")
                return True
            except Exception as e:
                logger.error(f"Failed to quarantine PDF {pdf_file.pdf_hash}: {e}")
                return (
                    True  # Still consider as quarantined to prevent further processing
                )
        else:
            # Archive the file normally
            logger.info(f"Archiving successfully processed PDF: {pdf_file.pdf_hash}")
            archive_dir = path_utils.PDF_DIR / "processed"
            os.makedirs(archive_dir, exist_ok=True)

            archive_path = archive_dir / f"{pdf_file.pdf_hash}.pdf"
            try:
                shutil.move(file_path, archive_path)
                logger.info(f"Moved processed PDF to archive: {archive_path}")
                return False
            except Exception as e:
                logger.error(f"Failed to archive PDF {pdf_file.pdf_hash}: {e}")
                return False
    
    def _is_placeholder_value(self, field_name: str, value) -> bool:
        """
        Determine whether a SensitiveMeta field contains a placeholder or default value.
        
        Parameters:
            field_name (str): Name of the SensitiveMeta field being checked (e.g., "patient_dob", "examination_date").
            value: The field's current value; may be None, a string, or a date.
        
        Returns:
            True if the value is considered a placeholder or default, False otherwise.
        """
        if value is None:
            return True

        # String placeholders
        if isinstance(value, str):
            if value in {self.DEFAULT_PATIENT_FIRST_NAME, self.DEFAULT_PATIENT_LAST_NAME}:
                return True

        # Date placeholders
        if isinstance(value, date):
            # Default DOB
            if field_name == "patient_dob" and value == self.DEFAULT_PATIENT_DOB:
                return True
            # "Today" exam date created as fallback – allow anonymizer to override
            if field_name == "examination_date" and value == date.today():
                return True

        return False
