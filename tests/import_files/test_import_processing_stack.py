"""Persisted native stack, automatic restart recovery and dispatcher priority."""
# pyright: reportPrivateUsage=false

from collections.abc import Generator
from datetime import timedelta
from unittest.mock import Mock
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, local
from uuid import uuid4

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.db import close_old_connections
from rest_framework.test import APIRequestFactory, force_authenticate

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.models import PortalUserInfo, UploadJob
from endoreg_db.models.state.report_import_attempt import ReportImportAttempt
from endoreg_db.schemas.import_processing import ImportProcessingStack
from endoreg_db.services.hub.upload_job_files import track_upload_job_files
from endoreg_db.services.hub.upload_job_import_lease import (
    reconcile_expired_upload_job_import_leases,
)
from endoreg_db.services.hub.upload_job_state_machine import mark_upload_job_completed
from endoreg_db.services.hub import upload_job_state_machine
from endoreg_db.services.hub.import_monitoring import dispatch_due_upload_job_retries
from endoreg_db.services.imports.checkpoints import (
    prepared_import_artifact,
    clear_import_checkpoint,
    media_import_checkpoint,
)
from endoreg_db.services.imports.processing import update_import_processing_stack
from endoreg_db.services.imports.recovery import (
    recover_persisted_import_stacks,
)
from endoreg_db.services.reports.import_fencing import acquire_report_import_fence
from endoreg_db.views.anonymization.overview import UploadJobRetryView
from endoreg_db import tasks
from endoreg_db.utils.hashs import get_bytes_hash
from tests.import_files.test_import_checkpoints import (
    context,
    failed_job,
    prepare,
    must_not_prepare,
)

__all__ = ["context"]
pytestmark = pytest.mark.django_db


@pytest.fixture
def tracked(context: ImportContext) -> Generator[tuple[ImportContext, UploadJob]]:
    job = failed_job(context)
    job.status = UploadJob.Status.PROCESSING
    job.error_code = UploadJob.ErrorCode.NONE
    job.save()
    with track_upload_job_files(job):
        yield context, job


def prepared_stack(ctx: ImportContext, job: UploadJob) -> ImportProcessingStack:
    with pytest.raises(RuntimeError, match="worker stopped"):
        with prepared_import_artifact(ctx, prepare):
            raise RuntimeError("worker stopped")
    job.refresh_from_db()
    stack = ImportProcessingStack.model_validate(job.processing_stack)
    assert stack.reference == job.pk
    assert stack.checkpoint is not None and stack.checkpoint.reference == job.pk
    assert stack.remaining == ["publish_artifact", "complete_upload"]
    return stack


def test_new_delivery_uses_fixed_checkpoint_and_remaining_stack(
    tracked: tuple[ImportContext, UploadJob],
) -> None:
    ctx, job = tracked
    saved = prepared_stack(ctx, job)
    instance = ctx.instance
    assert instance is not None
    instance.refresh_from_db()
    receipt = media_import_checkpoint(instance)
    assert receipt is not None and receipt.checkpoint_reference == job.pk
    # A new worker reads fresh model instances and has a different execution owner.
    restored = UploadJob.objects.get(pk=job.pk)
    ctx.attempt_id = uuid4().hex
    with track_upload_job_files(restored):
        with prepared_import_artifact(ctx, must_not_prepare):
            assert (
                ctx.anonymized_path is not None
                and ctx.anonymized_path.read_bytes() == b"prepared output"
            )
            clear_import_checkpoint(ctx)
    restored.refresh_from_db()
    published = ImportProcessingStack.model_validate(restored.processing_stack)
    assert (
        published.reference == saved.reference
        and published.checkpoint == saved.checkpoint
    )
    assert published.remaining == ["complete_upload"]
    mark_upload_job_completed(restored)
    restored.refresh_from_db()
    completed = ImportProcessingStack.model_validate(restored.processing_stack)
    assert completed.remaining == [] and completed.reference == saved.reference


def test_model_rejects_reordered_or_cross_job_stack(
    tracked: tuple[ImportContext, UploadJob],
) -> None:
    ctx, job = tracked
    saved = prepared_stack(ctx, job)
    for change in (
        {"remaining": ["complete_upload", "publish_artifact"]},
        {"reference": str(uuid4())},
    ):
        job.processing_stack = {**saved.model_dump(mode="json"), **change}
        with pytest.raises(ValidationError):
            job.save(update_fields=["processing_stack"])
        job.refresh_from_db()
        assert job.processing_stack == saved.model_dump(mode="json")


def test_missing_receipt_cannot_repeat_a_completed_stage(
    tracked: tuple[ImportContext, UploadJob],
) -> None:
    ctx, job = tracked
    prepared_stack(ctx, job)
    instance = ctx.instance
    assert instance is not None
    field = "meta" if ctx.current_video is not None else "raw_meta"
    payload = dict(getattr(instance, field) or {})
    payload.pop("import_checkpoint")
    setattr(instance, field, payload)
    instance.save(update_fields=[field])
    with pytest.raises(ValueError, match="prepare"):
        with prepared_import_artifact(ctx, must_not_prepare):
            pytest.fail(
                "Lost checkpoint receipt must not restart completed preparation"
            )


def test_restart_recovery_fences_expired_worker_and_retains_reference(
    tracked: tuple[ImportContext, UploadJob],
) -> None:
    ctx, job = tracked
    saved = prepared_stack(ctx, job)
    expired = timezone.now() - timedelta(minutes=2)
    job.last_attempt_at = expired
    job.save(update_fields=["last_attempt_at"])
    if ctx.current_video is not None:
        job.processing_lease_owner = "terminated-worker"
        job.processing_fencing_token = 1
        job.processing_heartbeat_at = expired
        job.processing_lease_expires_at = expired
        job.save()
        assert reconcile_expired_upload_job_import_leases() == 1
    else:
        assert ctx.file_hash is not None
        # Text uploads claim the normalized PDF identity, not the raw upload hash.
        job.content_type = "text/plain"
        job.content_hash = get_bytes_hash(b"original text upload")
        job.save(update_fields=["content_type", "content_hash"])
        acquire_report_import_fence(ctx.file_hash)
        ReportImportAttempt.objects.filter(content_hash=ctx.file_hash).update(
            lease_expires_at=expired, heartbeat_at=expired
        )
    assert recover_persisted_import_stacks() == 1
    job.refresh_from_db()
    assert job.status == UploadJob.Status.RETRYING
    assert ImportProcessingStack.model_validate(job.processing_stack) == saved
    assert job.retry_count == 1
    if ctx.current_report is not None:
        assert ReportImportAttempt.objects.filter(
            content_hash=ctx.file_hash, status=ReportImportAttempt.STATUS_FAILED
        ).exists()
    assert recover_persisted_import_stacks() == 0


def test_live_or_cancelled_report_cannot_be_recovered(
    tracked: tuple[ImportContext, UploadJob],
) -> None:
    ctx, job = tracked
    prepared_stack(ctx, job)
    if ctx.current_report is not None:
        assert ctx.file_hash is not None
        acquire_report_import_fence(ctx.file_hash)
    assert recover_persisted_import_stacks() == 0
    job.cancellation_requested_at = timezone.now()
    job.save(update_fields=["cancellation_requested_at"])
    assert recover_persisted_import_stacks() == 0


def test_priority_is_idempotent_and_precedes_other_dispatches(
    context: ImportContext,
) -> None:
    # Existing verified receipt can be adopted by a new durable upload stack.
    with prepared_import_artifact(context, prepare):
        pass
    boosted = failed_job(context)
    normal = UploadJob.objects.create(
        source_center=boosted.source_center,
        content_type=boosted.content_type,
        content_hash=get_bytes_hash(b"other-source"),
        file=SimpleUploadedFile("other" + context.file_path.suffix, b"other-source"),
    )
    normal.schedule_retry(
        "waiting", error_code=UploadJob.ErrorCode.PROCESSING_FAILED, delay_seconds=1
    )
    normal.next_retry_at = timezone.now() - timedelta(minutes=1)
    normal.save(update_fields=["next_retry_at"])
    update_import_processing_stack(normal.pk, "validate")
    user = User.objects.create_user(username="import-priority")
    instance = context.instance
    assert instance is not None
    PortalUserInfo.objects.create(user=user).centers.add(instance.center)
    for _ in range(2):
        request = APIRequestFactory().post(
            "/retry/", {"prioritize": True}, format="json"
        )
        force_authenticate(request, user=user)
        response = UploadJobRetryView.as_view(permission_classes=[])(
            request, job_id=boosted.pk
        )
        assert response.status_code == 202
    boosted.refresh_from_db()
    assert boosted.retry_count == 1
    stack = ImportProcessingStack.model_validate(boosted.processing_stack)
    assert stack.priority == 1
    boosted.next_retry_at = timezone.now() - timedelta(seconds=1)
    boosted.save(update_fields=["next_retry_at"])
    dispatcher = Mock()
    result = dispatch_due_upload_job_retries(
        dispatcher=dispatcher, queue="pipeline", limit=1
    )
    assert result.dispatched_count == 1
    assert dispatcher.apply_async.call_args.kwargs["args"] == (str(boosted.pk),)


@pytest.mark.django_db(transaction=True)
def test_two_failed_jobs_cannot_recover_the_same_content(
    context: ImportContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = failed_job(context), failed_job(context)
    barrier = Barrier(2)
    thread_state = local()
    check = upload_job_state_machine.recovery_identity_conflict

    def synchronize(job: UploadJob) -> bool:
        result = check(job)
        if not getattr(thread_state, "checked", False):
            thread_state.checked = True
            barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(
        upload_job_state_machine, "recovery_identity_conflict", synchronize
    )

    def admit(identifier: str) -> bool:
        close_old_connections()
        try:
            job = UploadJob.objects.get(pk=identifier)
            return upload_job_state_machine.admit_upload_job_recovery(
                job, "restart", delay_seconds=1
            )
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(admit, (str(first.pk), str(second.pk))))
    assert sorted(results) == [False, True]
    assert (
        UploadJob.objects.filter(
            pk__in=[first.pk, second.pk], status=UploadJob.Status.RETRYING
        ).count()
        == 1
    )


def test_worker_ready_uses_existing_maintenance_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatch = Mock()
    monkeypatch.setattr(tasks.current_app, "send_task", dispatch)
    tasks.resume_import_stacks_on_worker_restart()
    assert dispatch.call_args.args == ("endoreg_db.retry_due_upload_jobs",)
    assert dispatch.call_args.kwargs["queue"] == "maintenance"


def test_native_capability_missing_never_uses_python_fallback(
    tracked: tuple[ImportContext, UploadJob], monkeypatch: pytest.MonkeyPatch
) -> None:
    from endoreg_db.utils import rust_backend

    ctx, job = tracked

    def no_capabilities() -> list[tuple[str, str, str]]:
        return []

    monkeypatch.setattr(rust_backend, "_native_capabilities", no_capabilities)
    with pytest.raises(RuntimeError, match="import_processing_stack_v1"):
        with prepared_import_artifact(ctx, must_not_prepare):
            pytest.fail("Native capability must be mandatory")
    job.refresh_from_db()
    assert job.processing_stack is None


def test_report_restart_republishes_the_same_persisted_task(
    context: ImportContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if context.current_report is None:
        pytest.skip("Report-specific handoff recovery")
    from types import SimpleNamespace
    from endoreg_db.models import ReportLlmInferenceJob
    from endoreg_db.services.jobs.report_llm_jobs import dispatch_report_llm_import

    job = failed_job(context)
    job.status = UploadJob.Status.PROCESSING
    job.error_code = UploadJob.ErrorCode.NONE
    job.last_attempt_at = timezone.now() - timedelta(minutes=2)
    job.save()
    update_import_processing_stack(job.pk, "validate")

    def publish_task(**kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(id=kwargs["task_id"])

    publish = Mock(side_effect=publish_task)
    monkeypatch.setenv("REPORT_LLM_JOB_MODE", "celery")
    monkeypatch.setattr(tasks.run_report_llm_import_task, "apply_async", publish)
    first = dispatch_report_llm_import(upload_job_id=str(job.pk))
    assert first.status == "queued"
    assert recover_persisted_import_stacks() == 1
    resumed = dispatch_report_llm_import(
        upload_job_id=str(job.pk), resume_existing=True
    )
    assert resumed.status == "queued"
    assert resumed.job_id == first.job_id and resumed.task_id == first.task_id
    assert ReportLlmInferenceJob.objects.filter(upload_job=job).count() == 1
    assert publish.call_count == 2
    assert publish.call_args.kwargs["task_id"] == first.task_id


def test_report_claim_and_normalized_source_binding_commit_together(
    tracked: tuple[ImportContext, UploadJob],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, job = tracked
    if ctx.current_report is None:
        pytest.skip("Report-specific content claim")
    from endoreg_db.import_files import report_import_service

    service = report_import_service.ReportImportService.__new__(
        report_import_service.ReportImportService
    )
    service.lifecycle = None
    update = report_import_service.update_import_processing_stack

    def fail_after_binding(*args: object, **kwargs: object) -> None:
        raise RuntimeError("binding failed")

    monkeypatch.setattr(
        report_import_service, "update_import_processing_stack", fail_after_binding
    )
    with pytest.raises(RuntimeError, match="binding failed"):
        service._claim_import(ctx)
    assert not ReportImportAttempt.objects.filter(content_hash=ctx.file_hash).exists()
    monkeypatch.setattr(report_import_service, "update_import_processing_stack", update)
    fence = service._claim_import(ctx)
    job.refresh_from_db()
    assert fence.content_hash == ctx.file_hash
    assert (
        ImportProcessingStack.model_validate(job.processing_stack).source_sha256
        == ctx.file_hash
    )
