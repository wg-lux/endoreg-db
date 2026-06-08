# endoreg_db/services/anonymization.py
import logging
from datetime import datetime
from pathlib import Path
from typing import cast

from django.db import transaction
from lx_dtypes.models.contracts.anonymization import (
    AnonymizationListItemData,
    AnonymizationListItemPayload,
    AnonymizationStartResult,
    AnonymizationStatusData,
    AnonymizationStatusPayload,
    AnonymizationValidationResult,
    dump_anonymization_list_item_payload,
    dump_anonymization_status_payload,
)

from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.services.lx_video_contracts import resolve_lx_anonymization_state
from endoreg_db.services.video_import import VideoImportService
from endoreg_db.services.video_files import get_or_create_video_state
from endoreg_db.services.raw_pdf_files import get_or_create_raw_pdf_state
from endoreg_db.services.report_import import ReportImportService
from endoreg_db.utils.filesystem.paths import STORAGE_DIR
from endoreg_db.utils.storage import ensure_local_file, file_exists

logger = logging.getLogger(__name__)


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _required_text(value: object, *, fallback: str) -> str:
    return _optional_text(value) or fallback


def _optional_datetime(value: object) -> datetime | None:
    return value if isinstance(value, datetime) else None


def _related_object(instance: object, field_name: str) -> object | None:
    return cast(object | None, getattr(instance, field_name, None))


def _related_text(instance: object, relation_name: str, field_name: str) -> str | None:
    relation = _related_object(instance, relation_name)
    if relation is None:
        return None
    return _optional_text(getattr(relation, field_name, None))


def _state_anonymization_status(state: object | None) -> str:
    if state is None:
        return "not_started"
    return _required_text(
        getattr(state, "anonymization_status", None), fallback="not_started"
    )


def _state_is_anonymized(state: object | None) -> bool:
    return bool(getattr(state, "anonymized", False))


def _mark_state_processing_started(state: object | None) -> None:
    if state is None:
        return
    marker = getattr(state, "mark_processing_started", None)
    if callable(marker):
        marker()
        return
    setattr(state, "processing_started", True)
    saver = getattr(state, "save", None)
    if callable(saver):
        saver(update_fields=["processing_started"])


def _mark_state_processing_not_started(state: object | None) -> None:
    if state is None:
        return
    setattr(state, "processing_started", False)
    saver = getattr(state, "save", None)
    if callable(saver):
        saver(update_fields=["processing_started"])


def _mark_state_anonymization_validated(state: object) -> None:
    marker = getattr(state, "mark_anonymization_validated", None)
    if callable(marker):
        marker()


def _video_integrity_status(video: VideoFile) -> tuple[str, str]:
    payload = video.meta
    if payload is None:
        return "lost", "unknown"
    integrity_status = str(payload.get("integrity_status") or "").strip()
    integrity_error = str(payload.get("integrity_error") or "").strip()
    if not integrity_status and bool(
        getattr(getattr(video, "state", None), "processing_error", False)
    ):
        integrity_status = "lost"
    return integrity_status, integrity_error


def _video_has_integrity_failure(video: VideoFile) -> bool:
    integrity_status, _ = _video_integrity_status(video)
    return integrity_status == "lost" or bool(
        getattr(getattr(video, "state", None), "processing_error", False)
    )


class AnonymizationService:
    """
    Orchestrates long‑running anonymization tasks so the view only
    does HTTP <-> Service translation.
    """

    def __init__(self, project_root: Path | None = None):
        """
        Initialize the AnonymizationService with service instances.

        Args:
            project_root: Path to the project root. If None, uses settings.BASE_DIR
        """
        self.project_root: Path = project_root or STORAGE_DIR
        self.video_service = VideoImportService()
        self.pdf_service = ReportImportService()

    @staticmethod
    def get_status(
        file_id: int,
        kind: str | None = None,
    ) -> AnonymizationStatusData | None:
        """
        Retrieve status.
        Handles 'pdf' vs 'report' alias.
        If kind is None, checks both tables (Video priority).
        """

        # 1. Normalize the input kind if legacy name pdf is used
        if kind == "pdf":
            kind = "report"

        # 2. Define lookup logic
        check_video = kind == "video" or kind is None
        check_report = kind == "report" or kind is None

        # 3. Check VideoFile
        if check_video:
            vf = (
                VideoFile.objects.select_related("state", "sensitive_meta")
                .filter(pk=file_id)
                .first()
            )
            if vf:
                integrity_status, integrity_error = _video_integrity_status(vf)
                return dump_anonymization_status_payload(
                    AnonymizationStatusPayload(
                        media_type="video",
                        anonymization_status=resolve_lx_anonymization_state(vf).value,
                        integrity_status=integrity_status,
                        integrity_error=integrity_error,
                        file_exists=file_exists(vf.raw_file),
                        uuid=_optional_text(getattr(vf, "video_hash", None)),
                    )
                )

        # 4. Check RawPdfFile
        if check_report:
            pdf = (
                RawPdfFile.objects.select_related("state", "sensitive_meta")
                .filter(pk=file_id)
                .first()
            )
            if pdf:
                anonymization_status = _state_anonymization_status(
                    _related_object(pdf, "state")
                )
                return dump_anonymization_status_payload(
                    AnonymizationStatusPayload(
                        media_type="pdf",
                        anonymization_status=str(anonymization_status),
                        file_exists=file_exists(pdf.file),
                        hash=_optional_text(getattr(pdf, "pdf_hash", None)),
                    )
                )

        # 5. Not found in either (or the specific requested type wasn't found)
        return None

    # ---------- COMMANDS ------------------------------------------------
    @transaction.atomic
    def start(
        self,
        file_id: int,
        kind: str | None = None,
    ) -> AnonymizationStartResult | None:
        """
        Start anonymization process for a file by its ID.

        Args:
            file_id: The ID of the file to anonymize

        Returns:
            str or None: Media type if successful, None if file not found
        """
        # Try VideoFile first
        if kind == "video" or kind is None:
            vf = (
                VideoFile.objects.select_related(
                    "state", "sensitive_meta", "center", "video_meta__processor"
                )
                .filter(pk=file_id)
                .first()
            )
            if vf:
                try:
                    logger.info(
                        f"Starting video anonymization for VideoFile ID: {file_id}"
                    )

                    if _video_has_integrity_failure(vf):
                        integrity_status, integrity_error = _video_integrity_status(vf)
                        logger.error(
                            "Refusing anonymization for failed/lost VideoFile %s "
                            "(hash=%s, integrity_status=%s, reason=%s)",
                            file_id,
                            _optional_text(getattr(vf, "video_hash", None)),
                            integrity_status,
                            integrity_error,
                        )
                        return None

                    # Check if already processed
                    video_state = _related_object(vf, "state")
                    if _state_is_anonymized(video_state):
                        logger.info(f"VideoFile {file_id} already anonymized, skipping")
                        return "video"

                    raw_file = vf.raw_file
                    if not raw_file or not raw_file.name or not file_exists(raw_file):
                        logger.error(
                            "Raw file not found for VideoFile %s in storage",
                            file_id,
                        )
                        return None

                    # Get processor name
                    processor_name = _related_text(
                        _related_object(vf, "video_meta") or vf,
                        "processor",
                        "name",
                    )

                    # Get center name
                    center_name = _required_text(
                        _related_text(vf, "center", "name"),
                        fallback="unknown_center",
                    )

                    # Mark as started
                    _mark_state_processing_started(video_state)

                    # Use VideoImportService for anonymization
                    safe_processor_name = processor_name or "unknown_processor"
                    with ensure_local_file(raw_file) as file_path:
                        self.video_service.import_and_anonymize(
                            file_path=file_path,
                            center_name=center_name,
                            processor_name=safe_processor_name,
                        )

                    logger.info(
                        f"Video anonymization completed for VideoFile ID: {file_id}"
                    )
                    return "video"

                except Exception as e:
                    logger.error(f"Failed to anonymize VideoFile {file_id}: {e}")
                    # Mark as failed if state exists
                    _mark_state_processing_not_started(_related_object(vf, "state"))
                    raise
        if kind == "report" or kind is None:
            # Try RawPdfFile
            pdf = (
                RawPdfFile.objects.select_related("state", "sensitive_meta", "center")
                .filter(pk=file_id)
                .first()
            )
            if pdf:
                try:
                    logger.info(
                        f"Starting report processing for RawPdfFile ID: {file_id}"
                    )

                    # Check if already processed
                    pdf_state = _related_object(pdf, "state")
                    if _state_is_anonymized(pdf_state):
                        logger.info(f"RawPdfFile {file_id} already processed, skipping")
                        return "pdf"

                    file_field = pdf.file
                    if not file_field or not file_field.name:
                        logger.error(f"report file not found for RawPdfFile {file_id}")
                        return None

                    if not file_exists(file_field):
                        logger.error(
                            "report file missing from storage for RawPdfFile %s",
                            file_id,
                        )
                        return None

                    # Get center name
                    center_name = _required_text(
                        _related_text(pdf, "center", "name"),
                        fallback="unknown_center",
                    )

                    # Mark as started
                    _mark_state_processing_started(pdf_state)

                    with ensure_local_file(file_field) as local_path:
                        self.pdf_service.import_and_anonymize(
                            file_path=local_path,
                            center_name=center_name,
                        )

                    logger.info(
                        f"report processing completed for RawPdfFile ID: {file_id}"
                    )
                    return "pdf"

                except Exception as e:
                    logger.error(f"Failed to process RawPdfFile {file_id}: {e}")
                    # Mark as failed if state exists
                    failure_state = _related_object(pdf, "state") or _related_object(
                        pdf, "sensitive_meta"
                    )
                    saver = getattr(failure_state, "save", None)
                    if callable(saver):
                        saver(update_fields=["processing_failed"])
                    raise

            logger.warning(f"No file found with ID: {file_id}")
            return None

        return None

    @staticmethod
    @transaction.atomic
    def validate(file_id: int) -> AnonymizationValidationResult | None:
        vf = VideoFile.objects.select_related("state").filter(pk=file_id).first()
        if vf:
            video_state = _related_object(vf, "state") or get_or_create_video_state(vf)
            _mark_state_anonymization_validated(video_state)
            return "video"

        pdf = RawPdfFile.objects.select_related("state").filter(pk=file_id).first()
        if pdf:
            pdf_state = pdf.state or get_or_create_raw_pdf_state(pdf)
            if hasattr(pdf_state, "mark_anonymization_validated"):
                pdf_state.mark_anonymization_validated()
            return "pdf"

        return None

    @staticmethod
    def list_items() -> list[AnonymizationListItemData]:
        video_files = VideoFile.objects.select_related("state").all()
        pdf_files = RawPdfFile.objects.select_related(
            "state"
        ).all()  # was sensitive_meta

        data: list[AnonymizationListItemData] = []
        for vf in video_files:
            video_state = _related_object(vf, "state")
            data.append(
                dump_anonymization_list_item_payload(
                    AnonymizationListItemPayload(
                        id=int(vf.pk),
                        media_type="video",
                        anonymization_status=_state_anonymization_status(video_state),
                        created_at=_optional_datetime(
                            getattr(vf, "date_created", None)
                        ),
                        updated_at=_optional_datetime(
                            getattr(vf, "date_modified", None)
                        ),
                    ),
                )
            )

        for pdf in pdf_files:
            pdf_state = _related_object(pdf, "state")
            data.append(
                dump_anonymization_list_item_payload(
                    AnonymizationListItemPayload(
                        id=int(pdf.pk),
                        media_type="pdf",
                        anonymization_status=_state_anonymization_status(pdf_state),
                        created_at=_optional_datetime(
                            getattr(pdf, "date_created", None)
                        ),
                        updated_at=_optional_datetime(
                            getattr(pdf, "date_modified", None)
                        ),
                    ),
                )
            )
        return data
