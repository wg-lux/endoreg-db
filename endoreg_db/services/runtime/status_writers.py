"""Single writers of persisted lifecycle statuses decided by native tables.

Every writer reads the status persisted under the row lock, so a stale
in-memory instance never decides, and asks Rust ``decide_status_transition``.
It assigns the target when the table applies it and otherwise the persisted
value, so the instance never carries a stale status. Callers run inside
``transaction.atomic()`` and save the field together with their own changes.
Compare-and-set writers validate ``expected -> target`` natively before the
conditional update. See ``docs/state_mutation_ownership.md``.
"""

from __future__ import annotations

from enum import StrEnum
import re
from typing import TYPE_CHECKING

from django.db import models, transaction
from lx_dtypes.models.contracts.video_processing_history import (
    VideoProcessingHistoryStatus,
)

from endoreg_db.utils.rust_backend import decide_status_transition

if TYPE_CHECKING:
    from endoreg_db.models.media.pdf.report_llm_job import ReportLlmJobStatus
    from endoreg_db.models import (
        AIDataSetExportArtifact,
        DicomExportJob,
        OutboundHubTransferJob,
        PatientExaminationReport,
        QuarantineItem,
        ReportLlmInferenceJob,
        TransferJob,
        UploadJob,
        VideoProcessingHistory,
    )


class StatusMachine(StrEnum):
    """Mirror of the native ``status_machine`` vocabulary."""

    REPORT_LLM_INFERENCE_JOB = "report_llm_inference_job"
    VIDEO_PROCESSING_HISTORY = "video_processing_history"
    QUARANTINE_ITEM = "quarantine_item"
    FRAME_ANNOTATION_EXPORT_JOB = "frame_annotation_export_job"
    AI_DATASET_EXPORT_ARTIFACT = "ai_dataset_export_artifact"
    DICOM_EXPORT_JOB = "dicom_export_job"
    PATIENT_EXAMINATION_REPORT = "patient_examination_report"
    HUB_INBOUND_TRANSFER = "hub_inbound_transfer"
    HUB_INBOUND_CASE_RESOLUTION = "hub_inbound_case_resolution"
    UPLOAD_JOB = "upload_job"
    UPLOAD_JOB_LEASE_RECOVERY = "upload_job_lease_recovery"
    MODEL_TRAINING_RUN = "model_training_run"
    REPORT_IMPORT_ATTEMPT = "report_import_attempt"
    REPORT_IMPORT_LEASE_RECOVERY = "report_import_lease_recovery"
    UPLOAD_JOB_SOURCE_CLEANUP = "upload_job_source_cleanup"
    HUB_OUTBOUND_LOCAL_CLEANUP = "hub_outbound_local_cleanup"


_LIFECYCLE_FIELD = re.compile(r"^(?:\w+_)?(?:status|state)$|^failure_class$")


def _require_atomic() -> None:
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Lifecycle status changes require an atomic block")


def _locked_status[M: models.Model](
    manager: models.Manager[M], pk: object, field: str
) -> str:
    """Persisted ``field`` under the row lock; empty for an unsaved row."""
    _require_atomic()
    if pk is None:
        return ""
    return str(
        manager.select_for_update().filter(pk=pk).values_list(field, flat=True).get()
    )


def _decide(machine: StatusMachine, current: str, target: str) -> bool:
    return decide_status_transition(
        machine=machine.value, current=current, target=target
    )


def validate_status_transition(
    machine: StatusMachine, current: str, target: str, *, error_message: str
) -> bool:
    """Validate a native transition, retaining the caller's domain error contract."""
    try:
        return _decide(machine, current, target)
    except ValueError as exc:
        raise ValueError(error_message) from exc


def _require_applied(machine: StatusMachine, expected: str, target: str) -> None:
    """Compare-and-set writes only perform transitions the table applies."""
    if not _decide(machine, expected, target):
        raise ValueError(
            f"{machine.value}: {expected} -> {target} is not a state change"
        )


def _require_plain_fields(fields: dict[str, object]) -> None:
    lifecycle = sorted(name for name in fields if _LIFECYCLE_FIELD.match(name))
    if lifecycle:
        raise ValueError(f"lifecycle fields need their own writer: {lifecycle}")


def set_report_llm_job_status(
    job: ReportLlmInferenceJob, target: ReportLlmJobStatus
) -> bool:
    from endoreg_db.models import ReportLlmInferenceJob

    current = _locked_status(ReportLlmInferenceJob.objects, job.pk, "status")
    if not _decide(StatusMachine.REPORT_LLM_INFERENCE_JOB, current, target):
        return False
    job.status = target
    return True


def set_video_processing_history_status(
    history: VideoProcessingHistory, target: VideoProcessingHistoryStatus
) -> bool:
    from endoreg_db.models import VideoProcessingHistory

    current = _locked_status(VideoProcessingHistory.objects, history.pk, "status")
    if not _decide(StatusMachine.VIDEO_PROCESSING_HISTORY, current, target):
        return False
    history.status = target
    return True


def set_quarantine_item_status(item: QuarantineItem, target: str) -> bool:
    from endoreg_db.models import QuarantineItem

    current = _locked_status(QuarantineItem.objects, item.pk, "status")
    applied = _decide(StatusMachine.QUARANTINE_ITEM, current, target)
    # An ignored transition keeps the persisted value, so a later save of
    # this field cannot write back a stale in-memory status.
    item.status = target if applied else current
    return applied


def update_frame_export_job_status[M: models.Model](
    queryset: models.QuerySet[M],
    *,
    expected: str,
    target: str,
    **fields: object,
) -> int:
    """Conditionally move rows still in ``expected``; returns the updated count."""
    _require_applied(StatusMachine.FRAME_ANNOTATION_EXPORT_JOB, expected, target)
    _require_plain_fields(fields)
    return queryset.filter(status=expected).update(status=target, **fields)


def set_ai_dataset_export_artifact_status(
    artifact: AIDataSetExportArtifact, target: str
) -> bool:
    from endoreg_db.models import AIDataSetExportArtifact

    current = _locked_status(AIDataSetExportArtifact.objects, artifact.pk, "status")
    applied = _decide(StatusMachine.AI_DATASET_EXPORT_ARTIFACT, current, target)
    # An ignored transition keeps the persisted value, so a later save of
    # this field cannot write back a stale in-memory status.
    artifact.status = target if applied else current
    return applied


def set_dicom_export_job_status(job: DicomExportJob, target: str) -> bool:
    from endoreg_db.models import DicomExportJob

    current = _locked_status(DicomExportJob.objects, job.pk, "status")
    applied = _decide(StatusMachine.DICOM_EXPORT_JOB, current, target)
    # An ignored transition keeps the persisted value, so a later save of
    # this field cannot write back a stale in-memory status.
    job.status = target if applied else current
    return applied


def set_patient_examination_report_status(
    report: PatientExaminationReport, target: str
) -> bool:
    from endoreg_db.models import PatientExaminationReport

    current = _locked_status(PatientExaminationReport.objects, report.pk, "status")
    applied = _decide(StatusMachine.PATIENT_EXAMINATION_REPORT, current, target)
    # An ignored transition keeps the persisted value, so a later save of
    # this field cannot write back a stale in-memory status.
    report.status = target if applied else current
    return applied


def set_transfer_status(transfer_job: TransferJob, target: str) -> bool:
    from endoreg_db.models import TransferJob

    current = _locked_status(TransferJob.objects, transfer_job.pk, "transfer_status")
    applied = _decide(StatusMachine.HUB_INBOUND_TRANSFER, current, target)
    # An ignored transition keeps the persisted value, so a later save of
    # this field cannot write back a stale in-memory status.
    transfer_job.transfer_status = target if applied else current
    return applied


def update_transfer_status[M: models.Model](
    queryset: models.QuerySet[M],
    *,
    expected: str,
    target: str,
    case_resolution_status: str,
    **fields: object,
) -> int:
    """Fenced conditional transfer outcome.

    ``case_resolution_status`` is the value ``set_case_resolution_status``
    already decided on the in-memory job; it is persisted with the outcome.
    """
    _require_applied(StatusMachine.HUB_INBOUND_TRANSFER, expected, target)
    _require_plain_fields(fields)
    return queryset.filter(transfer_status=expected).update(
        transfer_status=target,
        case_resolution_status=case_resolution_status,
        **fields,
    )


def mark_transfers_lost[M: models.Model](
    queryset: models.QuerySet[M], **fields: object
) -> int:
    """Move applied transfers to lost after an integrity failure."""
    from endoreg_db.models import TransferJob

    applied = TransferJob.TransferStatus.APPLIED.value
    lost = TransferJob.TransferStatus.LOST.value
    _require_applied(StatusMachine.HUB_INBOUND_TRANSFER, applied, lost)
    _require_plain_fields(fields)
    return queryset.filter(transfer_status=applied).update(
        transfer_status=lost, **fields
    )


def set_case_resolution_status(transfer_job: TransferJob, target: str) -> bool:
    from endoreg_db.models import TransferJob

    current = _locked_status(
        TransferJob.objects, transfer_job.pk, "case_resolution_status"
    )
    applied = _decide(StatusMachine.HUB_INBOUND_CASE_RESOLUTION, current, target)
    # An ignored transition keeps the persisted value, so a later save of
    # this field cannot write back a stale in-memory status.
    transfer_job.case_resolution_status = target if applied else current
    return applied


def set_upload_job_cleanup_status(upload_job: UploadJob, target: str) -> bool:
    from endoreg_db.models import UploadJob

    current = _locked_status(UploadJob.objects, upload_job.pk, "cleanup_status")
    applied = _decide(StatusMachine.UPLOAD_JOB_SOURCE_CLEANUP, current, target)
    # An ignored transition keeps the persisted value, so a later save of
    # this field cannot write back a stale in-memory status.
    upload_job.cleanup_status = target if applied else current
    return applied


def set_outbound_local_cleanup_status(job: OutboundHubTransferJob, target: str) -> bool:
    from endoreg_db.models import OutboundHubTransferJob

    current = _locked_status(
        OutboundHubTransferJob.objects, job.pk, "local_cleanup_status"
    )
    applied = _decide(StatusMachine.HUB_OUTBOUND_LOCAL_CLEANUP, current, target)
    # An ignored transition keeps the persisted value, so a later save of
    # this field cannot write back a stale in-memory status.
    job.local_cleanup_status = target if applied else current
    return applied
