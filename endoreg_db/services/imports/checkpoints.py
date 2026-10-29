"""Encrypted preparation checkpoints shared by fenced video and report imports."""

from collections.abc import Callable, Generator
from contextlib import contextmanager
from functools import partial
import logging
from uuid import uuid4
from uuid import UUID
from typing import cast
from pathlib import Path

from django.db import transaction
from django.db.models.fields.files import FieldFile
from django.utils import timezone
from lx_dtypes.models.contracts.report_anonymization import ReportExtractionReceipt

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.import_files.file_storage.cleanup import cleanup_staging_files
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.models.state.anonymization import (
    AnonymizationPhase,
    anonymization_phase,
)
from endoreg_db.schemas.import_file import ImportCheckpoint
from endoreg_db.schemas.import_processing import ImportProcessingStack
from endoreg_db.services.hub.upload_job_state_machine import upload_job_recovery_filter
from endoreg_db.services.hub.upload_job_files import current_upload_job_id
from endoreg_db.services.imports.processing import (
    update_import_processing_stack,
    record_published_import,
    initialize_import_processing_stack,
    import_source_hash,
)
from endoreg_db.utils.encryption.storage_materialization import (
    materialized_plaintext_field_file,
)
from endoreg_db.utils.file_operations import safe_delete_field_file
from endoreg_db.utils.hashs import get_file_hash
from endoreg_db.utils.paths import get_runtime_paths, to_storage_relative
from endoreg_db.utils.storage import save_local_file
from endoreg_db.utils.storage import file_exists
from endoreg_db.utils.structured_logging import emit_structured_event

logger = logging.getLogger(__name__)


def media_import_checkpoint(
    instance: VideoFile | RawPdfFile,
) -> ImportCheckpoint | None:
    metadata = instance.meta if isinstance(instance, VideoFile) else instance.raw_meta
    payload = (metadata or {}).get("import_checkpoint")
    return ImportCheckpoint.model_validate(payload) if payload is not None else None


def has_pending_import_checkpoint(instance: VideoFile | RawPdfFile) -> bool:
    if media_import_checkpoint(instance) is None:
        return False
    state = instance.state
    if state is None:
        return True
    # The phase classifier calls Rust; checkpoint code owns no status mapping.
    match anonymization_phase(state.anonymization_status):
        case AnonymizationPhase.AWAITING_VALIDATION | AnonymizationPhase.VALIDATED:
            return False
        case (
            AnonymizationPhase.NOT_STARTED
            | AnonymizationPhase.IN_PROGRESS
            | AnonymizationPhase.FAILED
        ):
            return True


def checkpoint_retry_media(
    job: UploadJob, *, include_queued: bool = False
) -> VideoFile | RawPdfFile | None:
    """Admission hint; publication still authenticates bytes under a fresh fence."""
    center_id = cast(int | None, getattr(job, "source_center_id", None))
    if (
        not UploadJob.objects.filter(
            upload_job_recovery_filter(include_queued=include_queued), pk=job.pk
        ).exists()
        or not job.source_file_persisted
        or center_id is None
        or not job.content_hash
        or not job.file.name
    ):
        return None
    stack = (
        ImportProcessingStack.model_validate(job.processing_stack)
        if job.processing_stack is not None
        else None
    )
    source_hash = import_source_hash(job)
    if job.content_type.startswith("video/"):
        instance = VideoFile.objects.filter(
            raw_video_hash=source_hash, center_id=center_id
        ).first()
    else:
        instance = RawPdfFile.objects.filter(
            pdf_hash=source_hash, center_id=center_id
        ).first()
    if instance is None:
        return None
    if not has_pending_import_checkpoint(instance):
        return None
    checkpoint = media_import_checkpoint(instance)
    if (
        checkpoint is None
        or checkpoint.source_sha256 != source_hash
        or (
            stack is not None
            and stack.checkpoint is not None
            and stack.checkpoint.media_id != instance.pk
        )
        or checkpoint.sensitive_meta_id != instance.sensitive_meta_id
        or not file_exists(job.file)
    ):
        return None
    return instance


def _metadata(ctx: ImportContext) -> tuple[str, dict[str, object]]:
    instance = ctx.instance
    if instance is None:
        raise RuntimeError("Import checkpoint requires a persisted media instance")
    field = "meta" if isinstance(instance, VideoFile) else "raw_meta"
    return field, dict(getattr(instance, field) or {})


def _artifact(ctx: ImportContext, name: str) -> FieldFile:
    instance = ctx.instance
    if instance is None:
        raise RuntimeError("Import checkpoint requires a media instance")
    root = (
        get_runtime_paths().anonym_video
        if isinstance(instance, VideoFile)
        else get_runtime_paths().anonym_report
    )
    if name and str(Path(name).parent) != to_storage_relative(root):
        raise ValueError("Checkpoint artifact is outside its media checkpoint root")
    if not name:
        suffix = ".mp4" if isinstance(instance, VideoFile) else ".pdf"
        name = to_storage_relative(root / f"checkpoint-{uuid4().hex}{suffix}")
    return FieldFile(instance, instance.processed_file.field, name)


def _save_receipt(
    ctx: ImportContext, checkpoint: ImportCheckpoint | None, *, event: str | None = None
) -> None:
    with ctx.owned_mutation():
        field, metadata = _metadata(ctx)
        if checkpoint is None:
            metadata.pop("import_checkpoint", None)
        else:
            metadata["import_checkpoint"] = checkpoint.model_dump(mode="json")
        instance = ctx.instance
        assert instance is not None
        setattr(instance, field, metadata)
        instance.save(update_fields=[field])
        job_id = current_upload_job_id()
        if job_id is not None and (checkpoint is not None or event is not None):
            initialize_import_processing_stack(ctx)
            if event == "published":
                assert ctx.file_hash is not None
                record_published_import(
                    job_id, media_id=int(instance.pk), source_sha256=ctx.file_hash
                )
            else:
                update_import_processing_stack(
                    job_id,
                    "prepared",
                    media_id=int(instance.pk),
                )


def clear_import_checkpoint(ctx: ImportContext, *, published: bool = True) -> None:
    """Retire only after publication commits; rollback retains the encrypted copy."""
    _, metadata = _metadata(ctx)
    payload = metadata.get("import_checkpoint")
    if payload is None:
        return
    checkpoint = ImportCheckpoint.model_validate(payload)
    artifact = _artifact(ctx, checkpoint.artifact_name)
    _save_receipt(ctx, None, event="published" if published else None)
    transaction.on_commit(partial(safe_delete_field_file, artifact, missing_ok=False))


@contextmanager
def prepared_import_artifact(
    ctx: ImportContext,
    prepare: Callable[[ImportContext], ImportContext],
) -> Generator[ImportContext]:
    """Resume verified preparation, or checkpoint it before downstream publication.

    Explicit reprocessing discards the old receipt. Ordinary retry preserves it.
    Missing, changed or unauthentic artifacts fail closed rather than rerunning.
    """
    ctx.require_execution_ownership()
    job_id = current_upload_job_id()
    stack = initialize_import_processing_stack(ctx)
    if job_id is not None and ctx.force_reprocess:
        with ctx.owned_mutation():
            stack = update_import_processing_stack(job_id, "reset")
    _, metadata = _metadata(ctx)
    payload = metadata.get("import_checkpoint")
    if payload is not None and ctx.force_reprocess:
        clear_import_checkpoint(ctx, published=False)
        payload = None
    if payload is None:
        if job_id is not None:
            with ctx.owned_mutation():
                update_import_processing_stack(job_id, "prepare")
        ctx = prepare(ctx)
        prepared_path = ctx.anonymized_path
        try:
            ctx.require_execution_ownership()
            instance = ctx.instance
            if (
                instance is None
                or instance.sensitive_meta_id is None
                or prepared_path is None
                or ctx.file_hash is None
            ):
                raise RuntimeError(
                    "Prepared import lacks its artifact, source or sensitive metadata"
                )
            is_video = isinstance(instance, VideoFile)
            field, metadata = _metadata(ctx)
            extraction = None
            if not is_video:
                extraction = ReportExtractionReceipt.model_validate(
                    metadata.get("report_extraction_receipt")
                )
                if extraction.attempt_id != ctx.attempt_id:
                    raise RuntimeError(
                        "Report checkpoint extraction is not owned by this attempt"
                    )
            artifact = _artifact(ctx, "")
            # The schema is the single validation boundary for preparation evidence.
            checkpoint = ImportCheckpoint(
                checkpoint_reference=job_id
                if job_id is not None
                else UUID(str(instance.uuid)),
                media_type="video" if is_video else "report",
                source_sha256=ctx.file_hash,
                artifact_name=str(artifact.name),
                artifact_sha256=get_file_hash(prepared_path),
                sensitive_meta_id=instance.sensitive_meta_id,
                processor_name=ctx.processor_name,
                completed_at=timezone.now(),
                normalization=ctx.storage_normalization_evidence,
                extraction=extraction,
            )
            try:
                save_local_file(artifact, prepared_path, name=checkpoint.artifact_name)
                _save_receipt(ctx, checkpoint)
            except Exception:
                # Re-read persisted truth before deleting a uniquely named copy.
                # An uncertain database commit must retain its referenced artifact.
                instance.refresh_from_db(fields=[field])
                persisted = media_import_checkpoint(instance)
                if (
                    persisted is None
                    or persisted.artifact_name != checkpoint.artifact_name
                ):
                    safe_delete_field_file(artifact, missing_ok=True)
                raise
            emit_structured_event(
                logger,
                "import.checkpoint_saved",
                media_type=checkpoint.media_type,
                media_id=instance.pk,
            )
            yield ctx
        finally:
            cleanup_staging_files((prepared_path,), label="prepared import workspace")
        return

    checkpoint = ImportCheckpoint.model_validate(payload)
    instance = ctx.instance
    assert instance is not None
    media_type = "video" if isinstance(instance, VideoFile) else "report"
    if job_id is not None:
        if (
            checkpoint.checkpoint_reference is not None
            and checkpoint.checkpoint_reference != job_id
            and stack is not None
            and stack.checkpoint is not None
        ):
            raise RuntimeError(
                "Import checkpoint belongs to a different processing stack"
            )
        checkpoint = checkpoint.model_copy(update={"checkpoint_reference": job_id})
    if (
        checkpoint.source_sha256 != ctx.file_hash
        or checkpoint.media_type != media_type
        or checkpoint.sensitive_meta_id != instance.sensitive_meta_id
        or checkpoint.processor_name != ctx.processor_name
    ):
        raise RuntimeError("Import checkpoint no longer matches source or metadata")
    artifact = _artifact(ctx, checkpoint.artifact_name)
    suffix = ".mp4" if media_type == "video" else ".pdf"
    with materialized_plaintext_field_file(
        artifact, suffix=suffix, prefix="import-checkpoint-"
    ) as path:
        if get_file_hash(path) != checkpoint.artifact_sha256:
            raise RuntimeError("Import checkpoint artifact identity differs")
        ctx.require_execution_ownership()
        if job_id is not None:
            _save_receipt(ctx, checkpoint)
        if media_type == "report":
            extraction = checkpoint.extraction
            assert extraction is not None  # Enforced by ImportCheckpoint.
            # Adopt verified extraction under the new fence, retaining original
            # extraction ownership in the checkpoint until publication commits.
            with ctx.owned_mutation():
                field, metadata = _metadata(ctx)
                metadata["report_extraction_receipt"] = extraction.model_copy(
                    update={"attempt_id": ctx.attempt_id}
                ).model_dump(mode="json")
                setattr(instance, field, metadata)
                instance.save(update_fields=[field])
        ctx.anonymized_path = path
        ctx.storage_normalization_evidence = checkpoint.normalization
        emit_structured_event(
            logger,
            "import.checkpoint_resumed",
            media_type=media_type,
            media_id=instance.pk,
        )
        yield ctx
