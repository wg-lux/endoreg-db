"""Direct video imports acquire the same durable ownership as queued imports."""

import mimetypes
import uuid
from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path

from django.core.files.uploadedfile import UploadedFile
from django.db import transaction

from endoreg_db.import_files.context.file_lock import file_lock
from endoreg_db.import_files.video_import_service import (
    VideoImportService as ProcessingService,
    get_video_import_context_names,
    local_raw_source_context,
)
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.services.centers.defaults import resolve_import_center
from endoreg_db.services.hub.upload_job_cancellation import UploadJobImportCancelled
from endoreg_db.services.hub.upload_job_import_lease import (
    UploadJobImportLeaseHeartbeat,
    UploadJobImportLeaseLost,
    acquire_upload_job_import_lease,
    locked_upload_job_import_lease,
    release_upload_job_import_lease,
)
from endoreg_db.services.hub.upload_job_state_machine import (
    mark_upload_job_completed,
    mark_upload_job_error,
    mark_upload_job_processing,
)
from endoreg_db.services.imports.execution import ImportExecutionFence
from endoreg_db.services.jobs.error_handling import database_recovery_reason
from endoreg_db.utils.hashs import get_file_hash
from endoreg_db.utils.storage import ensure_local_file


class VideoImportService(ProcessingService):
    def import_and_anonymize(
        self,
        file_path: Path | str,
        center_name: str,
        processor_name: str,
        retry: bool = False,
    ) -> VideoFile:
        return self._import_owned(file_path, center_name, processor_name, retry=retry)

    def reanonymize_existing_video(
        self,
        video: VideoFile,
        *,
        source_path: Path | str | None = None,
        prepare: Callable[[], None] | None = None,
    ) -> VideoFile:
        center_name, processor_name = get_video_import_context_names(video)
        source_context = (
            local_raw_source_context(video)
            if source_path is None
            else nullcontext(Path(source_path))
        )
        with source_context as local_path:
            return self._import_owned(
                local_path,
                center_name,
                processor_name,
                retry=True,
                video=video,
                prepare=prepare,
            )

    def _import_owned(
        self,
        file_path: Path | str,
        center_name: str,
        processor_name: str,
        *,
        retry: bool,
        video: VideoFile | None = None,
        prepare: Callable[[], None] | None = None,
    ) -> VideoFile:
        # A background heartbeat requires independently committed ownership.
        if not transaction.get_autocommit():
            raise RuntimeError("Direct video import requires autocommit")
        from endoreg_db.services.hub.ingest import create_or_reuse_upload_job

        path = Path(file_path)
        content_type, _ = mimetypes.guess_type(path.name)
        if content_type is None or not content_type.startswith("video/"):
            raise ValueError("Direct video import requires a supported video source")
        if not processor_name.strip():
            raise ValueError("Direct video import requires a processor name")
        center = resolve_import_center(center_name)
        with file_lock(path), path.open("rb") as source:
            content_hash = get_file_hash(path)
            job, _ = create_or_reuse_upload_job(
                uploaded_file=UploadedFile(
                    source, name=path.name, content_type=content_type
                ),
                content_type=content_type,
                source_center=center,
                source_system="direct_video_import",
                content_hash=content_hash,
                processing_provenance={
                    "entrypoint": "direct_video_import",
                    "processor_name": processor_name,
                },
            )
        attempt_id = uuid.uuid4().hex
        lease = acquire_upload_job_import_lease(
            upload_job_id=str(job.pk), owner=f"execution-{attempt_id}"
        )
        try:
            with UploadJobImportLeaseHeartbeat(lease) as heartbeat:
                with locked_upload_job_import_lease(heartbeat.lease) as owned_job:
                    mark_upload_job_processing(owned_job)
                with ensure_local_file(job.file) as local_path:
                    if get_file_hash(local_path) != content_hash:
                        raise RuntimeError(
                            "Persisted video source does not match its content hash"
                        )
                    fence = ImportExecutionFence(
                        attempt_id=attempt_id,
                        guard=heartbeat.guard,
                        mutation_guard=heartbeat.mutation_guard,
                    )
                    if prepare is not None:
                        with fence.mutation_guard():
                            prepare()
                    if video is None:
                        video = self.import_and_anonymize_fenced(
                            local_path,
                            center_name,
                            processor_name,
                            retry=retry,
                            execution_fence=fence,
                        )
                    else:
                        video = self.reanonymize_existing_video_fenced(
                            video,
                            source_path=local_path,
                            execution_fence=fence,
                        )
                with locked_upload_job_import_lease(heartbeat.lease) as owned_job:
                    mark_upload_job_completed(
                        owned_job, sensitive_meta=video.sensitive_meta
                    )
        except Exception as exc:
            # Uncertain database state and lost authority belong to recovery.
            if database_recovery_reason(exc) is not None or isinstance(
                exc, (UploadJobImportLeaseLost, UploadJobImportCancelled)
            ):
                raise
            try:
                with locked_upload_job_import_lease(lease) as owned_job:
                    mark_upload_job_error(owned_job, str(exc))
                release_upload_job_import_lease(lease)
            except (
                UploadJobImportLeaseLost,
                UploadJobImportCancelled,
            ) as ownership_error:
                raise ownership_error from exc
            raise
        release_upload_job_import_lease(lease)
        return video
