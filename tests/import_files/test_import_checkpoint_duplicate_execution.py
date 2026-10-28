"""Checkpointed imports must not execute a completed stage or admission twice."""

from collections.abc import Callable, Generator
from contextlib import contextmanager, AbstractContextManager
from pathlib import Path
from unittest.mock import Mock

from django.contrib.auth.models import User
from django.db.models.fields.files import FieldFile
import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.models import PortalUserInfo, UploadJob
from endoreg_db.services.hub.upload_job_import_lease import UploadJobImportLeaseLost
from endoreg_db.services.hub.upload_job_state_machine import (
    validate_upload_job_status_transition,
)
from endoreg_db.services.imports.checkpoints import (
    checkpoint_retry_media,
    clear_import_checkpoint,
    media_import_checkpoint,
    prepared_import_artifact,
)
from endoreg_db.services.imports import checkpoints
from endoreg_db.services.runtime.lifecycle_state_machine import (
    OperationLifecycleEvent,
    OperationLifecycleState,
    transition_operation_lifecycle,
)
from endoreg_db.utils.file_operations import safe_unlink_file
from endoreg_db.utils.paths import get_runtime_paths
from endoreg_db.utils.rust_backend import has_native_capability
from endoreg_db.views.anonymization.overview import UploadJobRetryView
from tests.import_files.test_import_checkpoints import (
    checkpoint,
    context,
    must_not_prepare,
    prepare,
    failed_job,
)

pytestmark = pytest.mark.django_db

__all__ = ["context"]


def checkpoint_artifacts(ctx: ImportContext) -> set[Path]:
    paths = get_runtime_paths()
    root = paths.anonym_video if ctx.current_video is not None else paths.anonym_report
    return set(root.glob("checkpoint-*")) if root.exists() else set()


def counting(
    stage: Callable[[ImportContext], ImportContext], calls: list[str]
) -> Callable[[ImportContext], ImportContext]:
    def run(ctx: ImportContext) -> ImportContext:
        calls.append(ctx.attempt_id)
        return stage(ctx)

    return run


def test_completed_preparation_runs_once_across_repeated_resumes(
    context: ImportContext,
) -> None:
    before = checkpoint_artifacts(context)
    calls: list[str] = []
    with prepared_import_artifact(context, counting(prepare, calls)) as prepared:
        assert prepared.anonymized_path is not None
        safe_unlink_file(prepared.anonymized_path)
    instance = context.instance
    assert instance is not None
    instance.refresh_from_db()
    receipt = media_import_checkpoint(instance)
    assert receipt is not None

    for _ in range(3):
        with prepared_import_artifact(context, counting(must_not_prepare, calls)):
            pass

    assert len(calls) == 1
    assert len(checkpoint_artifacts(context) - before) == 1
    instance.refresh_from_db()
    assert media_import_checkpoint(instance) == receipt


def test_superseded_owner_cannot_write_a_second_checkpoint(
    context: ImportContext,
) -> None:
    before = checkpoint_artifacts(context)
    checks = 0

    def superseded_after_prepare() -> None:
        nonlocal checks
        checks += 1
        if checks > 1:
            raise UploadJobImportLeaseLost("superseded during preparation")

    context.execution_guard = superseded_after_prepare
    try:
        with pytest.raises(UploadJobImportLeaseLost):
            with prepared_import_artifact(context, prepare):
                pytest.fail("A superseded owner entered publication")
    finally:
        if context.anonymized_path is not None:
            safe_unlink_file(context.anonymized_path, missing_ok=True)
    instance = context.instance
    assert instance is not None
    instance.refresh_from_db()
    assert media_import_checkpoint(instance) is None
    assert checkpoint_artifacts(context) == before


@pytest.mark.parametrize("boundary", ["artifact", "receipt"])
def test_ownership_lost_while_recording_receipt_leaves_no_orphan_artifact(
    context: ImportContext,
    boundary: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed receipt write must not leave a stored artifact for a later attempt to duplicate."""
    before = checkpoint_artifacts(context)
    reject = Mock(
        side_effect=UploadJobImportLeaseLost("superseded while recording checkpoint")
    )

    @contextmanager
    def lost_mutation() -> Generator[None]:
        reject()
        yield

    if boundary == "receipt":
        context.mutation_guard = lost_mutation
    else:
        save = checkpoints.save_local_file

        def save_then_lose_ownership(
            field_file: FieldFile, source: Path, *, name: str
        ) -> str:
            save(field_file, source, name=name)
            raise UploadJobImportLeaseLost("superseded while publishing checkpoint")

        monkeypatch.setattr(checkpoints, "save_local_file", save_then_lose_ownership)
    try:
        with pytest.raises(UploadJobImportLeaseLost):
            with prepared_import_artifact(context, prepare):
                pytest.fail("A checkpoint without a receipt entered publication")
    finally:
        if context.anonymized_path is not None:
            safe_unlink_file(context.anonymized_path, missing_ok=True)
    instance = context.instance
    assert instance is not None
    instance.refresh_from_db()
    assert media_import_checkpoint(instance) is None
    assert checkpoint_artifacts(context) == before


def test_explicit_reprocess_replaces_instead_of_accumulating_checkpoints(
    context: ImportContext,
    django_capture_on_commit_callbacks: Callable[..., AbstractContextManager[object]],
) -> None:
    before = checkpoint_artifacts(context)
    first = checkpoint(context)
    instance = context.instance
    assert instance is not None
    old_path = Path(instance.processed_file.storage.path(first.artifact_name))
    calls: list[str] = []
    context.force_reprocess = True
    with django_capture_on_commit_callbacks(execute=True):
        with prepared_import_artifact(context, counting(prepare, calls)) as prepared:
            assert prepared.anonymized_path is not None
            safe_unlink_file(prepared.anonymized_path)
    instance.refresh_from_db()
    second = media_import_checkpoint(instance)
    assert second is not None and second.artifact_name != first.artifact_name
    assert len(calls) == 1
    assert not old_path.exists()
    assert len(checkpoint_artifacts(context) - before) == 1


def test_double_submitted_resume_is_admitted_once(context: ImportContext) -> None:
    checkpoint(context)
    job = failed_job(context)
    instance = context.instance
    assert instance is not None
    user = User.objects.create_user(username="checkpoint-double-submit")
    PortalUserInfo.objects.create(user=user).centers.add(instance.center)
    view = UploadJobRetryView.as_view(permission_classes=[])
    statuses: list[int] = []
    for _ in range(2):
        request = APIRequestFactory().post("/retry/")
        force_authenticate(request, user=user)
        statuses.append(view(request, job_id=job.pk).status_code)
    assert statuses[0] == 202
    assert statuses[1] in {202, 409}
    job.refresh_from_db()
    assert job.status == UploadJob.Status.RETRYING
    assert job.retry_count == 1
    instance.refresh_from_db()
    assert media_import_checkpoint(instance) is not None


def test_live_lease_or_retired_checkpoint_blocks_resume_admission(
    context: ImportContext,
    django_capture_on_commit_callbacks: Callable[..., AbstractContextManager[object]],
) -> None:
    checkpoint(context)
    job = failed_job(context)
    assert checkpoint_retry_media(job) == context.instance
    job.processing_lease_owner = "live-worker"
    assert checkpoint_retry_media(job) is None
    job.processing_lease_owner = ""
    # Publication committed: replaying the resume must not republish the media.
    with django_capture_on_commit_callbacks(execute=True):
        clear_import_checkpoint(context)
    assert checkpoint_retry_media(job) is None


def test_native_lifecycle_rejects_repeated_resume_request() -> None:
    """Resume admission is reduced by the Rust operation lifecycle, not a Python copy."""
    assert has_native_capability("lifecycle_state_machine", "lifecycle_state_v3")
    validate_upload_job_status_transition(
        current_status=UploadJob.Status.ERROR.value,
        target_status=UploadJob.Status.RETRYING.value,
    )
    with pytest.raises(ValueError, match="invalid operation lifecycle transition"):
        transition_operation_lifecycle(
            OperationLifecycleState.RETRY_WAIT, OperationLifecycleEvent.RETRY_REQUESTED
        )
    with pytest.raises(ValueError, match="invalid operation lifecycle transition"):
        transition_operation_lifecycle(
            OperationLifecycleState.SUCCEEDED, OperationLifecycleEvent.START
        )
