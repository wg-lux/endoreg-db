# endoreg_db/services/base_import.py
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Optional, Union

from endoreg_db.import_files.file_lock import _file_lock

logger = logging.getLogger(__name__)


class BaseImportService(ABC):
    """
    Base class for media import services (PDF, Video, ...).

    Provides:
      - shared processing_context dict
      - per-instance processed_files tracking
      - generalized _initialize_processing_context()
      - generic file lock around the whole pipeline
    """

    def __init__(self) -> None:
        self.processing_context: Dict[str, Any] = {}
        self.processed_files: set[str] = set()
        self.original_path: Optional[Path] = None
        self.file_type: str = "None"
        self.error_cleanup: ErrorCleanup = ErrorCleanup()

    # === Template method (orchestrator) ===
    def import_and_anonymize(
        self,
        file_path: Union[Path, str],
        **context_kwargs: Any,
    ):
        """
        Orchestrate the full import/anonymization pipeline.

        Subclasses normally expose their own public method that calls *this* method,
        e.g.:

            class VideoImportService(BaseImportService):
                def import_and_anonymize_video(...):
                    return self.import_and_anonymize(file_path, center_name=center_name, ...)
        """
        try:
            # 1) Initialize generic processing context (including duplicate check)
            self._initialize_processing_context(file_path=file_path, **context_kwargs)

            path_to_lock = self.original_path or self.processing_context.get("file_path")
            if path_to_lock is None:
                raise ValueError("No file path set before acquiring file lock")

            with _file_lock(Path(path_to_lock), self._cleanup_on_error()):
                logger.info("Acquired file lock for %s", path_to_lock)

                self._validate_and_prepare_file()
                self._create_or_retrieve_instance()
                if not self._has_instance():
                    logger.warning("No instance created for %s; aborting", path_to_lock)
                    return None

                self._setup_processing_environment()
                self._process_payload()
                self._finalize_processing()

                # Mark as processed in this service instance (session-level guard)
                self.processed_files.add(str(Path(file_path)))
                return self._get_instance()

        except Exception:
            # Let subclasses do detailed cleanup (state, files, etc.)
            self._cleanup_on_error()
            raise
        finally:
            self._cleanup_processing_context()

    # === Generic context init (reused by all subclasses) ===
    def _initialize_processing_context(
        self,
        file_path: Union[Path, str],
        **extra: Any,
    ) -> None:
        """
        Generic context initialization.

        Sets:
          - original_path
          - processing_context["file_path"]
          - processing_context["processing_started"] = False
          - processing_context["anonymization_completed"] = False
          - processing_context.update(extra)
        And performs a simple per-instance duplicate check using processed_files.
        """
        path = Path(file_path)
        self.original_path = path

        # Per-service-session guard: don't process the same path twice in one instance
        if str(path) in self.processed_files:
            logger.info(
                "File %s already processed/being processed in this service instance; skipping",
                path,
            )
            raise ValueError("File already being processed")

        # Base keys that make sense for all media types
        self.processing_context = {
            "file_path": path,
            "processing_started": False,
            "anonymization_completed": False,
        }

        # Allow subclasses to pass media-specific stuff (center_name, processor_name, ...)
        if extra:
            self.processing_context.update(extra)

        logger.info("Starting import and processing for: %s", path)

    # === Abstract hooks for subclasses ===
    @abstractmethod
    def _validate_and_prepare_file(self) -> None:
        """Media-specific validation (existence, hash, etc.)."""

    @abstractmethod
    def _create_or_retrieve_instance(self) -> None:
        """Create or load VideoFile/RawPdfFile/etc."""

    @abstractmethod
    def _has_instance(self) -> bool:
        """Return True if a media instance is ready."""

    @abstractmethod
    def _get_instance(self):
        """Return the created/loaded media instance."""

    @abstractmethod
    def _setup_processing_environment(self) -> None:
        """Set up any temporary dirs, state, sensitive copy, etc."""

    @abstractmethod
    def _process_payload(self) -> None:
        """Run anonymization + metadata extraction (core work)."""

    @abstractmethod
    def _finalize_processing(self) -> None:
        """Write back state flags, commit DB changes, etc."""

    @abstractmethod
    def _cleanup_on_error(self) -> None:
        """Rollback state / files on error."""

    @abstractmethod
    def _cleanup_processing_context(self) -> None:
        """Tear down temp dirs, clear context, etc."""
