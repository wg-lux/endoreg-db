"""Persist native import stack transitions; stage order remains exclusively in Rust."""

from uuid import UUID
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.import_files.context.import_context import ImportContext
from django.db import transaction
from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.schemas.import_processing import ImportProcessingStack
from endoreg_db.utils.rust_backend import (
    new_import_processing_stack,
    import_processing_stack_transition,
)


def import_source_hash(job: UploadJob) -> str:
    """Use the native normalized source binding; unbound legacy jobs use intake identity."""
    if job.processing_stack is not None:
        stack = ImportProcessingStack.model_validate(job.processing_stack)
        if stack.source_sha256 is not None:
            return stack.source_sha256
    return job.content_hash


@transaction.atomic
def update_import_processing_stack(
    job_id: UUID,
    event: str,
    *,
    media_id: int | None = None,
    source_sha256: str | None = None,
) -> ImportProcessingStack:
    job = UploadJob.objects.select_for_update().get(pk=job_id)
    if job.processing_stack is None:
        if job.content_type.startswith("video/"):
            kind = "video"
        elif job.content_type in {"application/pdf", "text/plain"}:
            kind = "report"
        else:
            raise ValueError(
                "Upload media type does not support an import processing stack"
            )
        stack = ImportProcessingStack.model_validate_json(
            new_import_processing_stack(str(job.pk), kind)
        )
    else:
        stack = ImportProcessingStack.model_validate(job.processing_stack)
    next_stack = ImportProcessingStack.model_validate_json(
        import_processing_stack_transition(
            stack.model_dump_json(), event, media_id, source_sha256
        )
    )
    if job.processing_stack != next_stack.model_dump(mode="json"):
        job.processing_stack = next_stack.model_dump(mode="json")
        job.save(update_fields=["processing_stack", "updated_at"])
    return next_stack


def initialize_import_processing_stack(
    ctx: "ImportContext",
) -> ImportProcessingStack | None:
    from endoreg_db.services.hub.upload_job_files import current_upload_job_id

    job_id = current_upload_job_id()
    if job_id is None:
        return None  # Direct standalone imports have no Celery delivery to resume.
    with ctx.owned_mutation():
        if ctx.file_hash is not None:
            return update_import_processing_stack(
                job_id, "bind_source", source_sha256=ctx.file_hash
            )
        return update_import_processing_stack(job_id, "validate")


@transaction.atomic
def record_published_import(job_id: UUID, *, media_id: int, source_sha256: str) -> None:
    """Adopt an independently verified published artifact through native events."""
    update_import_processing_stack(job_id, "bind_source", source_sha256=source_sha256)
    update_import_processing_stack(job_id, "adopt_published", media_id=media_id)


def complete_import_processing_stack(job: UploadJob) -> None:
    """Called in the existing fenced UploadJob completion transaction."""
    current = UploadJob.objects.only("processing_stack").get(pk=job.pk)
    if current.processing_stack is not None:
        stack = update_import_processing_stack(job.pk, "completed")
        job.processing_stack = stack.model_dump(mode="json")


def prioritize_checkpoint(job: UploadJob) -> bool:
    """Prefer eligible queued recovery without preempting active work."""
    from endoreg_db.services.imports.checkpoints import checkpoint_retry_media
    from endoreg_db.services.hub.upload_job_state_machine import (
        admit_upload_job_recovery,
    )

    if checkpoint_retry_media(job, include_queued=True) is None:
        return False
    if job.status == UploadJob.Status.ERROR and not admit_upload_job_recovery(
        job, "Prioritized checkpoint recovery.", delay_seconds=1
    ):
        return False
    stack = update_import_processing_stack(job.pk, "prioritize")
    job.processing_stack = stack.model_dump(mode="json")
    return True
