from __future__ import annotations

# pyright: reportPrivateUsage=false

import uuid
from contextlib import nullcontext
from datetime import timedelta
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, Mock, patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from endoreg_db import tasks
from endoreg_db.import_files.video_import_service import VideoImportExecutionFence
from endoreg_db.models import Center, UploadJob
from endoreg_db.schemas.report_llm import ReportLlmDispatchResult
from endoreg_db.services.hub.ingest import (
    _VideoUploadImportAttempt,
    _execute_video_upload_import_attempt,
    _import_fenced_video_upload,
    process_upload_job,
)
from endoreg_db.services.hub.upload_job_import_lease import UploadJobImportLease
from endoreg_db.utils.file_operations import atomic_write_file


@pytest.fixture
def ingest_center(db: None) -> Center:
    return Center.objects.create(
        name="ingest-job-contract-center",
        display_name="Ingest Job Contract Center",
    )


def _create_upload_job(
    *,
    center: Center | None,
    content_type: str,
    filename: str,
) -> UploadJob:
    return UploadJob.objects.create(
        file=SimpleUploadedFile(
            name=filename,
            content=b"contract-source",
            content_type=content_type,
        ),
        content_type=content_type,
        source_center=center,
        source_system="contract-test",
        processing_provenance={"entrypoint": "api"},
    )


def _report_dispatch_result(
    status: str,
    *,
    reason: str | None = None,
) -> ReportLlmDispatchResult:
    return ReportLlmDispatchResult.model_validate(
        {
            "task_id": "report-task-id",
            "mode": "celery",
            "status": status,
            "operation": "report_llm_import",
            "queue": "llm_inference",
            "job_id": "report-job-id",
            "reason": reason,
        }
    )


class TestReportImportJobHandoff:
    @pytest.mark.django_db
    def test_dispatches_report_upload_with_the_import_job_contract(
        self,
        ingest_center: Center,
    ) -> None:
        # Arrange
        upload_job = _create_upload_job(
            center=ingest_center,
            content_type="application/pdf",
            filename="report.pdf",
        )
        dispatch_result = _report_dispatch_result("queued")

        # Act
        with patch(
            "endoreg_db.services.jobs.report_llm_jobs.dispatch_report_llm_import",
            return_value=dispatch_result,
        ) as dispatch:
            processed = process_upload_job(str(upload_job.pk))

        # Assert
        assert processed is True
        dispatch.assert_called_once_with(
            upload_job_id=str(upload_job.pk),
            payload={"source": "upload_job"},
        )

    @pytest.mark.django_db
    @pytest.mark.parametrize("status", ["already_queued", "completed"])
    def test_accepts_idempotent_report_job_results(
        self,
        ingest_center: Center,
        status: str,
    ) -> None:
        # Arrange
        upload_job = _create_upload_job(
            center=ingest_center,
            content_type="application/pdf",
            filename=f"{status}.pdf",
        )

        # Act
        with patch(
            "endoreg_db.services.jobs.report_llm_jobs.dispatch_report_llm_import",
            return_value=_report_dispatch_result(status),
        ):
            processed = process_upload_job(str(upload_job.pk))

        # Assert
        assert processed is True
        assert (
            UploadJob.objects.get(pk=upload_job.pk).status
            == UploadJob.Status.PROCESSING
        )

    @pytest.mark.django_db
    def test_maps_lost_report_job_result_to_lost_upload(
        self,
        ingest_center: Center,
    ) -> None:
        # Arrange
        upload_job = _create_upload_job(
            center=ingest_center,
            content_type="application/pdf",
            filename="lost.pdf",
        )

        # Act
        with patch(
            "endoreg_db.services.jobs.report_llm_jobs.dispatch_report_llm_import",
            return_value=_report_dispatch_result("lost", reason="source missing"),
        ):
            processed = process_upload_job(str(upload_job.pk))

        # Assert
        upload_job.refresh_from_db()
        assert processed is False
        assert upload_job.status == UploadJob.Status.LOST

    @pytest.mark.django_db
    def test_maps_failed_report_job_result_to_error_upload(
        self,
        ingest_center: Center,
    ) -> None:
        # Arrange
        upload_job = _create_upload_job(
            center=ingest_center,
            content_type="application/pdf",
            filename="failed.pdf",
        )

        # Act
        with patch(
            "endoreg_db.services.jobs.report_llm_jobs.dispatch_report_llm_import",
            return_value=_report_dispatch_result("failed", reason="worker rejected"),
        ):
            processed = process_upload_job(str(upload_job.pk))

        # Assert
        upload_job.refresh_from_db()
        assert processed is False
        assert upload_job.status == UploadJob.Status.ERROR


class TestVideoImportJobHandoff:
    @pytest.mark.django_db
    def test_dispatches_video_upload_with_reserved_task_identity(
        self,
        ingest_center: Center,
    ) -> None:
        # Arrange
        upload_job = _create_upload_job(
            center=ingest_center,
            content_type="video/mp4",
            filename="video.mp4",
        )
        task_uuid = uuid.UUID("12345678-1234-5678-1234-567812345678")
        dispatcher = Mock()
        dispatcher.apply_async.return_value = Mock(id=task_uuid.hex)

        # Act
        with (
            patch("endoreg_db.services.hub.ingest.uuid.uuid4", return_value=task_uuid),
            patch(
                "endoreg_db.services.hub.ingest.queue_for_job_kind",
                return_value="ffmpeg_media",
            ),
            patch(
                "endoreg_db.services.hub.ingest.ensure_secure_transport_for_job_kind"
            ),
            patch(
                "endoreg_db.services.hub.ingest._video_upload_import_task_dispatcher",
                return_value=dispatcher,
            ),
        ):
            processed = process_upload_job(str(upload_job.pk))

        # Assert
        assert processed is True
        dispatcher.apply_async.assert_called_once_with(
            args=(str(upload_job.pk),),
            queue="ffmpeg_media",
            routing_key="ffmpeg_media",
            task_id=task_uuid.hex,
        )

    @pytest.mark.django_db
    def test_passes_fenced_attempt_contract_to_video_import_service(
        self,
        ingest_center: Center,
        tmp_path: Path,
    ) -> None:
        # Arrange
        upload_job = _create_upload_job(
            center=ingest_center,
            content_type="video/mp4",
            filename="fenced.mp4",
        )
        owner = "video-task-owner"
        lease = UploadJobImportLease(
            upload_job_id=str(upload_job.pk),
            owner=owner,
            fencing_epoch=7,
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        attempt = _VideoUploadImportAttempt(
            job_id=str(upload_job.pk),
            job=upload_job,
            lease=lease,
            owner=owner,
        )
        heartbeat = Mock()
        source_path = tmp_path / "fenced.mp4"
        atomic_write_file(destination=source_path, content=(b"video",))
        expected_attempt_id = uuid.uuid5(uuid.NAMESPACE_URL, owner).hex

        # Act
        with (
            patch(
                "endoreg_db.services.hub.ingest._required_video_upload_processor_name",
                return_value="processor",
            ),
            patch(
                "endoreg_db.services.video_import.VideoImportService"
            ) as service_class,
        ):
            result = _import_fenced_video_upload(
                attempt=attempt,
                heartbeat=heartbeat,
                file_path=source_path,
                center=ingest_center,
                provenance={},
            )

        # Assert
        assert (
            result
            is service_class.return_value.import_and_anonymize_fenced.return_value
        )
        service_class.return_value.import_and_anonymize_fenced.assert_called_once()
        call_kwargs = (
            service_class.return_value.import_and_anonymize_fenced.call_args.kwargs
        )
        assert call_kwargs["file_path"] == source_path
        assert call_kwargs["center_name"] == ingest_center.name
        assert call_kwargs["processor_name"] == "processor"
        assert call_kwargs["retry"] is False
        execution_fence = call_kwargs["execution_fence"]
        assert isinstance(execution_fence, VideoImportExecutionFence)
        assert execution_fence.attempt_id == expected_attempt_id
        assert execution_fence.guard == heartbeat.guard

    @pytest.mark.django_db
    def test_rechecks_video_lease_after_import_service_error(
        self,
        ingest_center: Center,
        tmp_path: Path,
    ) -> None:
        # Arrange
        upload_job = _create_upload_job(
            center=ingest_center,
            content_type="video/mp4",
            filename="failing.mp4",
        )
        lease = UploadJobImportLease(
            upload_job_id=str(upload_job.pk),
            owner="failing-owner",
            fencing_epoch=2,
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        attempt = _VideoUploadImportAttempt(
            job_id=str(upload_job.pk),
            job=upload_job,
            lease=lease,
            owner=lease.owner,
        )
        heartbeat = Mock()
        source_path = tmp_path / "failing.mp4"
        atomic_write_file(destination=source_path, content=(b"video",))

        # Act / Assert
        with (
            patch(
                "endoreg_db.services.hub.ingest._required_video_upload_processor_name",
                return_value="processor",
            ),
            patch(
                "endoreg_db.services.video_import.VideoImportService.import_and_anonymize_fenced",
                side_effect=RuntimeError("import failed"),
            ),
            pytest.raises(RuntimeError, match="import failed"),
        ):
            _import_fenced_video_upload(
                attempt=attempt,
                heartbeat=heartbeat,
                file_path=source_path,
                center=ingest_center,
                provenance={},
            )

        heartbeat.guard.assert_called_once_with()

    @pytest.mark.django_db
    def test_releases_video_lease_before_source_cleanup(
        self,
        ingest_center: Center,
        tmp_path: Path,
    ) -> None:
        upload_job = _create_upload_job(
            center=ingest_center,
            content_type="video/mp4",
            filename="cleanup-order.mp4",
        )
        lease = UploadJobImportLease(
            upload_job_id=str(upload_job.pk),
            owner="cleanup-order-owner",
            fencing_epoch=3,
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        attempt = _VideoUploadImportAttempt(
            job_id=str(upload_job.pk),
            job=upload_job,
            lease=lease,
            owner=lease.owner,
        )
        heartbeat = Mock(lease=lease)
        heartbeat_manager = MagicMock()
        heartbeat_manager.__enter__.return_value = heartbeat
        source_path = tmp_path / "cleanup-order.mp4"
        atomic_write_file(destination=source_path, content=(b"video",))
        events: list[str] = []

        def record_heartbeat_exit(*_args: object) -> None:
            events.append("heartbeat_stopped")

        def record_lease_release(_lease: UploadJobImportLease) -> None:
            events.append("lease_released")

        def record_source_cleanup(_job: UploadJob) -> None:
            events.append("source_cleaned")

        def record_prediction_dispatch(**_kwargs: object) -> None:
            events.append("prediction_dispatched")

        heartbeat_manager.__exit__.side_effect = record_heartbeat_exit

        with (
            patch(
                "endoreg_db.services.hub.ingest._validate_fenced_video_upload_source",
                return_value=(upload_job, ingest_center),
            ),
            patch(
                "endoreg_db.services.hub.ingest.UploadJobImportLeaseHeartbeat",
                return_value=heartbeat_manager,
            ),
            patch(
                "endoreg_db.services.hub.ingest._mark_fenced_video_upload_processing",
                return_value=(upload_job, {}),
            ),
            patch(
                "endoreg_db.services.hub.ingest._ensure_upload_job_local_file",
                return_value=nullcontext(source_path),
            ),
            patch(
                "endoreg_db.services.hub.ingest._import_fenced_video_upload",
                return_value=None,
            ),
            patch(
                "endoreg_db.services.hub.ingest._complete_fenced_video_upload",
                return_value=upload_job,
            ),
            patch(
                "endoreg_db.services.hub.ingest.release_upload_job_import_lease",
                side_effect=record_lease_release,
            ),
            patch(
                "endoreg_db.services.hub.ingest.cleanup_upload_job_source",
                side_effect=record_source_cleanup,
            ),
            patch(
                "endoreg_db.services.hub.ingest._dispatch_video_upload_prediction",
                side_effect=record_prediction_dispatch,
            ),
        ):
            result = _execute_video_upload_import_attempt(attempt)

        assert result is True
        assert events == [
            "heartbeat_stopped",
            "lease_released",
            "source_cleaned",
            "prediction_dispatched",
        ]


class TestInvalidIngestJobInputs:
    @pytest.mark.django_db
    def test_marks_upload_without_stored_file_as_lost(
        self,
        ingest_center: Center,
    ) -> None:
        # Arrange
        upload_job = UploadJob.objects.create(
            file="",
            content_type="application/pdf",
            source_center=ingest_center,
        )

        # Act
        processed = process_upload_job(str(upload_job.pk))

        # Assert
        upload_job.refresh_from_db()
        assert processed is False
        assert upload_job.status == UploadJob.Status.LOST

    @pytest.mark.django_db
    def test_marks_upload_without_center_as_configuration_error(self) -> None:
        # Arrange
        upload_job = _create_upload_job(
            center=None,
            content_type="application/pdf",
            filename="no-center.pdf",
        )

        # Act
        processed = process_upload_job(str(upload_job.pk))

        # Assert
        upload_job.refresh_from_db()
        assert processed is False
        assert upload_job.error_code == UploadJob.ErrorCode.INVALID_CONFIGURATION


class TestCeleryImportTaskAdapters:
    def test_video_task_forwards_request_id_as_lease_owner(self) -> None:
        # Arrange
        task = cast(Any, tasks.run_video_upload_import_task)
        task.push_request(id="video-worker-task-id")

        try:
            # Act
            with patch(
                "endoreg_db.services.hub.ingest._run_video_upload_import_job",
                return_value=True,
            ) as run_import:
                result = task.run(123)
        finally:
            task.pop_request()

        # Assert
        assert result is True
        run_import.assert_called_once_with("123", lease_owner="video-worker-task-id")

    def test_report_task_normalizes_and_forwards_job_id(self) -> None:
        # Arrange / Act
        with patch(
            "endoreg_db.services.jobs.report_llm_jobs._run_report_llm_import_job",
            return_value=True,
        ) as run_import:
            result = cast(Any, tasks.run_report_llm_import_task).run(123)

        # Assert
        assert result is True
        run_import.assert_called_once_with("123")
