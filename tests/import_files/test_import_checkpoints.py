"""Real encrypted checkpoint persistence and retry boundaries for both media kinds."""
# pyright: reportPrivateUsage=false

from collections.abc import Callable
from contextlib import nullcontext, AbstractContextManager
from pathlib import Path
from uuid import uuid4
from typing import cast
from unittest.mock import Mock
from functools import partial
import logging

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import models, transaction
import pytest
from cryptography.exceptions import InvalidTag
from rest_framework.test import APIRequestFactory, force_authenticate
from lx_dtypes.models.contracts.report_anonymization import (
    ReportExtractionEvidence,
    ReportExtractionReceipt,
)

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.models import (
    Center,
    PortalUserInfo,
    RawPdfFile,
    SensitiveMeta,
    UploadJob,
    VideoFile,
    VideoState,
    RawPdfState,
)
from endoreg_db.schemas.import_file import ImportCheckpoint
from endoreg_db.services.imports.checkpoints import (
    checkpoint_retry_media,
    clear_import_checkpoint,
    media_import_checkpoint,
    prepared_import_artifact,
    has_pending_import_checkpoint,
)
from endoreg_db.utils.encryption.encrypted import EncryptedStorage
from endoreg_db.utils.file_operations import atomic_write_file, safe_unlink_file
from endoreg_db.utils.hashs import get_file_hash
from endoreg_db.utils.paths import get_runtime_paths
from endoreg_db.utils.storage import save_local_file
from endoreg_db.views.anonymization.overview import UploadJobRetryView
from endoreg_db.serializers.misc.file_overview import FileOverviewSerializer
from endoreg_db.import_files import video_import_service, report_import_service
from endoreg_db.services.imports.execution import ImportExecutionFence
from endoreg_db.services.reports.import_fencing import (
    acquire_report_import_fence,
    mark_report_import_fence_failed,
    report_import_mutation_guard,
)
from pydantic import ValidationError
from tests.import_files.test_video_finalize_success import build_normalization_evidence

pytestmark = pytest.mark.django_db


@pytest.fixture(params=["video", "report"])
def context(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> ImportContext:
    kind = "video" if request.param == "video" else "report"
    root = get_runtime_paths().storage
    storage = EncryptedStorage(location=root, master_key=b"a" * 32)
    model = VideoFile if kind == "video" else RawPdfFile
    for name in (
        ("processed_file", "raw_file") if kind == "video" else ("processed_file",)
    ):
        field = model._meta.get_field(name)
        assert isinstance(field, models.FileField)
        monkeypatch.setattr(field, "storage", storage)
    center = Center.objects.create(name=f"checkpoint-{uuid4().hex}")
    # Identity derivation is outside this storage test; persist a bare fixture
    # through Django's base save instead of invoking clinical matching.
    sensitive = SensitiveMeta(center=center)
    models.Model.save(sensitive)
    source = root / f"source-{uuid4().hex}{'.mp4' if kind == 'video' else '.pdf'}"
    atomic_write_file(destination=source, content=(b"source",), required_bytes=6)
    ctx = ImportContext(
        file_path=source,
        file_type=kind,
        center_name=center.name,
        file_hash=get_file_hash(source),
        mutation_guard=nullcontext,
    )
    if kind == "video":
        ctx.current_video = VideoFile.objects.create(
            center=center, raw_video_hash=ctx.file_hash, sensitive_meta=sensitive
        )
        save_local_file(ctx.current_video.raw_file, source, save=True)
    else:
        ctx.current_report = RawPdfFile.objects.create(
            center=center, pdf_hash=ctx.file_hash, sensitive_meta=sensitive
        )
    return ctx


def prepare(ctx: ImportContext) -> ImportContext:
    output = (
        get_runtime_paths().transcoding
        / f"prepared-{uuid4().hex}{ctx.file_path.suffix}"
    )
    atomic_write_file(
        destination=output, content=(b"prepared output",), required_bytes=15
    )
    ctx.anonymized_path = output
    if ctx.current_video is not None:
        ctx.storage_normalization_evidence = build_normalization_evidence()
    else:
        assert ctx.current_report is not None
        assert ctx.file_hash is not None
        receipt = ReportExtractionReceipt(
            attempt_id=ctx.attempt_id,
            source_sha256=ctx.file_hash,
            staged_artifact_sha256=get_file_hash(output),
            extraction_evidence=ReportExtractionEvidence(
                outcome="fallback",
                backend="spacy_regex",
                finding_count=0,
                provider_attempted=False,
            ),
        )
        ctx.current_report.raw_meta = {
            "report_extraction_receipt": receipt.model_dump(mode="json")
        }
        ctx.current_report.save(update_fields=["raw_meta"])
    return ctx


def must_not_prepare(ctx: ImportContext) -> ImportContext:
    raise AssertionError("A completed preparation stage must not run twice")


def failed_job(ctx: ImportContext, *, retry_count: int = 0) -> UploadJob:
    instance = ctx.instance
    assert instance is not None
    return UploadJob.objects.create(
        source_center=instance.center,
        content_hash=ctx.file_hash,
        content_type="video/mp4"
        if ctx.current_video is not None
        else "application/pdf",
        file=SimpleUploadedFile("source" + ctx.file_path.suffix, b"source"),
        source_file_persisted=True,
        status=UploadJob.Status.ERROR,
        error_code=UploadJob.ErrorCode.IMPORT_INTERRUPTED,
        retry_count=retry_count,
    )


def checkpoint(ctx: ImportContext) -> ImportCheckpoint:
    with pytest.raises(RuntimeError, match="publication interrupted"):
        with prepared_import_artifact(ctx, prepare):
            raise RuntimeError("publication interrupted")
    instance = ctx.instance
    assert instance is not None
    instance.refresh_from_db()
    receipt = media_import_checkpoint(instance)
    assert receipt is not None
    assert (
        Path(instance.processed_file.storage.path(receipt.artifact_name))
        .read_bytes()
        .startswith(b"LXENC01")
    )
    assert not instance.processed_file.name
    assert ctx.anonymized_path is not None
    assert not ctx.anonymized_path.exists()
    safe_unlink_file(ctx.anonymized_path, missing_ok=True)
    ctx.anonymized_path = None
    ctx.attempt_id = uuid4().hex
    return receipt


@pytest.mark.parametrize("consumer_fails", [False, True])
def test_checkpoint_survives_restart_and_removes_scoped_plaintext(
    context: ImportContext, consumer_fails: bool
) -> None:
    receipt = checkpoint(context)
    root = get_runtime_paths().transcoding
    before = set(root.iterdir())
    path: Path | None = None
    try:
        with prepared_import_artifact(context, must_not_prepare) as resumed:
            path = resumed.anonymized_path
            assert path is not None
            assert path.read_bytes() == b"prepared output"
            assert path.stat().st_mode & 0o777 == 0o600
            if resumed.current_report is not None:
                adopted = ReportExtractionReceipt.model_validate(
                    (resumed.current_report.raw_meta or {})["report_extraction_receipt"]
                )
                assert adopted.attempt_id == resumed.attempt_id
                assert (
                    receipt.extraction is not None
                    and receipt.extraction.attempt_id != adopted.attempt_id
                )
            if consumer_fails:
                raise RuntimeError("publication interrupted")
    except RuntimeError as error:
        assert consumer_fails and str(error) == "publication interrupted"
    assert path is not None and not path.exists()
    assert set(root.iterdir()) == before
    assert context.instance is not None
    context.instance.refresh_from_db()
    assert media_import_checkpoint(context.instance) == receipt


@pytest.mark.parametrize(
    "defect", ["tamper", "wrong_key", "missing", "source", "metadata", "stale_owner"]
)
def test_checkpoint_rejects_invalid_recovery(
    context: ImportContext, defect: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipt = checkpoint(context)
    instance = context.instance
    assert instance is not None
    path = Path(instance.processed_file.storage.path(receipt.artifact_name))
    if defect == "tamper":
        data = bytearray(path.read_bytes())
        data[-1] ^= 1
        atomic_write_file(
            destination=path, content=(bytes(data),), required_bytes=len(data)
        )
    elif defect == "wrong_key":
        monkeypatch.setattr(
            instance.processed_file.field,
            "storage",
            EncryptedStorage(
                location=get_runtime_paths().storage, master_key=b"b" * 32
            ),
        )
    elif defect == "missing":
        safe_unlink_file(path)
    elif defect == "source":
        context.file_hash = "b" * 64
    elif defect == "metadata":
        instance.sensitive_meta = None
    else:

        def lost() -> None:
            raise RuntimeError("stale owner")

        context.execution_guard = lost
    before = set(get_runtime_paths().transcoding.iterdir())
    with pytest.raises((RuntimeError, ValueError, OSError, InvalidTag)):
        with prepared_import_artifact(context, must_not_prepare):
            pytest.fail("Invalid checkpoint admitted")
    assert set(get_runtime_paths().transcoding.iterdir()) == before


def test_checkpoint_retirement_waits_for_commit_and_survives_rollback(
    context: ImportContext,
    django_capture_on_commit_callbacks: Callable[..., AbstractContextManager[object]],
) -> None:
    receipt = checkpoint(context)
    instance = context.instance
    assert instance is not None
    path = Path(instance.processed_file.storage.path(receipt.artifact_name))
    with pytest.raises(RuntimeError, match="rollback"):
        with transaction.atomic():
            clear_import_checkpoint(context)
            assert path.exists()
            raise RuntimeError("rollback")
    instance.refresh_from_db()
    assert media_import_checkpoint(instance) == receipt
    # pytest-django's surrounding transaction does not commit automatically.
    with django_capture_on_commit_callbacks(execute=True):
        clear_import_checkpoint(context)
        assert path.exists()
    assert not path.exists()
    instance.refresh_from_db()
    assert media_import_checkpoint(instance) is None


@pytest.mark.parametrize("wrong_center", [False, True])
@pytest.mark.parametrize("exhausted", [False, True])
def test_resume_endpoint_preserves_scope_and_schedules_checkpoint(
    context: ImportContext, wrong_center: bool, exhausted: bool
) -> None:
    checkpoint(context)
    instance = context.instance
    assert instance is not None
    job = failed_job(context, retry_count=3 if exhausted else 0)
    assert checkpoint_retry_media(job) == (None if exhausted else instance)
    setattr(instance, "_overview_upload_job", job)
    summary = cast(
        dict[str, object],
        FileOverviewSerializer().to_representation(instance)["import_checkpoint"],
    )
    assert set(summary) == {
        "stage",
        "completed_at",
        "can_resume",
        "can_prioritize",
        "prioritized",
    }
    assert summary["can_resume"] is (not exhausted)
    user = User.objects.create_user(username="checkpoint-operator")
    assigned = Center.objects.create(name="other") if wrong_center else instance.center
    PortalUserInfo.objects.create(user=user).centers.add(assigned)
    request = APIRequestFactory().post("/retry/")
    force_authenticate(request, user=user)
    response = UploadJobRetryView.as_view(permission_classes=[])(request, job_id=job.pk)
    assert response.status_code == (403 if wrong_center else 409 if exhausted else 202)
    job.refresh_from_db()
    assert job.status == (
        UploadJob.Status.ERROR
        if wrong_center or exhausted
        else UploadJob.Status.RETRYING
    )
    if not wrong_center:
        assert job.retry_count == (3 if exhausted else 1)
        assert job.max_retries == 3
    instance.refresh_from_db()
    assert media_import_checkpoint(instance) is not None


@pytest.mark.parametrize(
    "defect", ["traversal", "master", "media_type", "evidence", "version"]
)
def test_checkpoint_contract_rejects_invalid_persisted_receipts(
    context: ImportContext, defect: str
) -> None:
    receipt = checkpoint(context)
    payload = receipt.model_dump(mode="json")
    if defect == "traversal":
        payload["artifact_name"] = "../" + receipt.artifact_name
    elif defect == "master":
        payload["artifact_name"] = "processed_videos/master.mp4"
    elif defect == "media_type":
        payload["media_type"] = "report" if receipt.media_type == "video" else "video"
    elif defect == "evidence":
        payload["normalization" if receipt.media_type == "video" else "extraction"] = (
            None
        )
    else:
        payload["version"] = 2
    with pytest.raises(ValidationError):
        ImportCheckpoint.model_validate(payload)


def test_coordinators_resume_after_publication_failure(
    context: ImportContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    preparation = Mock(side_effect=prepare)
    publication = Mock(side_effect=[RuntimeError("publication interrupted"), None])
    if context.current_video is not None:
        service = object.__new__(video_import_service.VideoImportService)
        monkeypatch.setattr(service, "_prepare_video_artifact", preparation)
        monkeypatch.setattr(video_import_service, "finalize_video_success", publication)
        with pytest.raises(RuntimeError, match="publication interrupted"):
            service._anonymize_and_finalize(context)
        assert media_import_checkpoint(context.current_video) is not None
        context.attempt_id = uuid4().hex
        # The ordinary import entrypoint sets this from failure history.
        context.retry = True
        assert service._anonymize_and_finalize(context) == context.current_video
    else:
        report_service = object.__new__(report_import_service.ReportImportService)
        report_service.lifecycle = None
        report_service.logger = logging.getLogger(__name__)
        report_service.anonymizer = Mock(anonymize_report=preparation)
        monkeypatch.setattr(
            report_import_service, "finalize_report_success", publication
        )
        monkeypatch.setattr(
            report_import_service, "record_report_finding_presence", Mock()
        )
        assert context.file_hash is not None
        for first_attempt in (True, False):
            fence = acquire_report_import_fence(context.file_hash)
            context.bind_execution_fence(
                ImportExecutionFence(
                    attempt_id=fence.owner_id.hex,
                    guard=lambda: None,
                    mutation_guard=partial(report_import_mutation_guard, fence),
                )
            )
            if first_attempt:
                with pytest.raises(RuntimeError, match="publication interrupted"):
                    report_service._anonymize_and_finalize(context, fence)
                assert mark_report_import_fence_failed(fence)
            else:
                assert (
                    report_service._anonymize_and_finalize(context, fence)
                    == context.current_report
                )
    preparation.assert_called_once()
    assert publication.call_count == 2
    instance = context.instance
    assert instance is not None
    instance.refresh_from_db()
    assert media_import_checkpoint(instance) is None


@pytest.mark.parametrize("validated", [False, True])
def test_committed_or_validated_media_is_not_reopened_by_a_leftover_checkpoint(
    context: ImportContext, validated: bool
) -> None:
    checkpoint(context)
    instance = context.instance
    assert instance is not None
    flags = {
        "anonymized": True,
        "sensitive_meta_processed": True,
        "anonymization_validated": validated,
    }
    if isinstance(instance, VideoFile):
        instance.state = VideoState.objects.create(**flags)
    else:
        instance.state = RawPdfState.objects.create(**flags)
    instance.save(update_fields=["state"])
    assert has_pending_import_checkpoint(instance) is False
    assert (
        FileOverviewSerializer().to_representation(instance)["import_checkpoint"]
        is None
    )
    assert checkpoint_retry_media(failed_job(context)) is None
    state = instance.state
    assert state is not None
    state.refresh_from_db()
    assert state.anonymization_validated is validated
