from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from django.contrib.auth.models import Group, User
from django.core.files.base import ContentFile, File
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError, connection, transaction
from django.db.models import Model
from django.test import Client, RequestFactory
from rest_framework.test import APIRequestFactory, force_authenticate

from endoreg_db.models import (
    Center,
    Label,
    LabelVideoSegment,
    PortalUserInfo,
    RawPdfFile,
    VideoFile,
)
from endoreg_db.models.media.pdf.pdf_processing_history import PdfProcessingHistory
from endoreg_db.models.media.operation_lease import MediaOperationLease
from endoreg_db.models.operation_log import OperationLog
from endoreg_db.models.state.raw_pdf import RawPdfState
from endoreg_db.models.state.video import VideoState
from endoreg_db.services.privacy.workflow import AnonymizationService
from endoreg_db.utils.operation_log import record_operation
from endoreg_db.utils.hashs import get_file_hash
from endoreg_db.utils.rust_backend import render_single_page_pdf
from endoreg_db.views.anonymization.overview import start_anonymization
from endoreg_db.views.anonymization.validate import AnonymizationValidateView
from endoreg_db.services.video_storage import generation_cleanup
from tests.helpers.default_objects import get_default_processor

from endoreg_db.views.video.segments_crud import (
    video_segment_validate,
    video_segments_validate_bulk,
)
from tests.helpers.media_states import create_media_state

if TYPE_CHECKING:
    from django.db.models.fields.related_descriptors import ManyRelatedManager


@contextmanager
def fail_audit_insert(*, fail_at: int = 1) -> Generator[None]:
    """Fail the actual audit SQL with a missing-table error, not a mocked save."""
    insert_count = 0

    def execute(
        wrapped: Callable[..., object],
        sql: str,
        params: object,
        many: bool,
        context: dict[str, object],
    ) -> object:
        nonlocal insert_count
        if (
            sql.lstrip().upper().startswith("INSERT INTO")
            and OperationLog._meta.db_table in sql
        ):
            insert_count += 1
            if insert_count == fail_at:
                return wrapped(
                    "INSERT INTO endoreg_missing_audit_storage (id) VALUES (1)",
                    None,
                    False,
                    context,
                )
        return wrapped(sql, params, many, context)

    with connection.execute_wrapper(execute):
        yield
    assert insert_count >= fail_at


def test_record_operation_persists_required_evidence() -> None:
    request = RequestFactory().post("/operation/")
    record_operation(request, action="segment.annotated", resource_id=42)
    operation = OperationLog.objects.get()
    assert (operation.action, operation.resource_id, operation.http_method) == (
        "segment.annotated",
        42,
        "POST",
    )


def test_record_operation_propagates_database_failure() -> None:
    with fail_audit_insert(), pytest.raises(DatabaseError), transaction.atomic():
        record_operation(
            RequestFactory().post("/operation/"), action="segment.annotated"
        )
    assert not OperationLog.objects.exists()


@pytest.mark.parametrize("bulk", [False, True])
def test_segment_validation_and_audit_roll_back_together(bulk: bool) -> None:
    center = Center.objects.create(name="atomic-segment-audit")
    video = VideoFile.objects.create(
        center=center, raw_video_hash="atomic-segment-audit"
    )
    label = Label.objects.create(name="audit-label")
    segments = [
        LabelVideoSegment.objects.create(
            video_file=video,
            label=label,
            start_frame_number=start,
            end_frame_number=start + 2,
        )
        for start in ([2, 6] if bulk else [2])
    ]
    for segment in segments:
        segment.get_or_create_state()
    source_ids = [getattr(segment, "source_id") for segment in segments]
    request = APIRequestFactory().post(
        "/segments/validate/",
        {"is_validated": True, "segment_ids": [segment.pk for segment in segments]},
        format="json",
    )
    with fail_audit_insert(fail_at=len(segments)):
        if bulk:
            response = video_segments_validate_bulk(request, pk=video.pk)
        else:
            response = video_segment_validate(
                request, pk=video.pk, segment_id=segments[0].pk
            )
    assert response.status_code == 500
    for segment, source_id in zip(segments, source_ids, strict=True):
        segment.refresh_from_db()
        assert not segment.state.is_validated
        assert getattr(segment, "source_id") == source_id
    assert not OperationLog.objects.exists()
    assert not MediaOperationLease.objects.filter(video=video).exists()


def test_pdf_redaction_database_changes_roll_back_when_audit_storage_fails() -> None:
    center = Center.objects.create(name="atomic-redaction-audit")
    content = render_single_page_pdf("Sample report")
    assert content is not None
    state = create_media_state(
        RawPdfState,
        anonymized=True,
        sensitive_meta_processed=True,
        anonymization_validated=True,
    )
    pdf = RawPdfFile.objects.create(
        center=center,
        pdf_hash="atomic-redaction-audit",
        file=SimpleUploadedFile("source.pdf", content, content_type="application/pdf"),
        state=state,
    )
    state_before = RawPdfState.objects.values().get(pk=state.pk)
    pdf.refresh_from_db()
    processed_before = pdf.processed_file.name
    with fail_audit_insert():
        response = Client().post(
            f"/api/media/pdfs/{pdf.pk}/apply-redactions/",
            {
                "file": SimpleUploadedFile(
                    "redacted.pdf", content, content_type="application/pdf"
                ),
                "source_type": "raw",
                "redaction_manifest": '{"version":1,"pages":[],"normalized":true}',
            },
        )
    assert response.status_code == 500
    pdf.refresh_from_db()
    assert pdf.processed_file.name == processed_before
    assert RawPdfState.objects.values().get(pk=state.pk) == state_before
    assert not PdfProcessingHistory.objects.filter(pdf=pdf).exists()
    assert not OperationLog.objects.exists()
    # Database rollback does not imply rollback of encrypted filesystem writes.


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("fail_at", [None, 1, 2])
def test_start_audits_request_before_autocommit_work(
    monkeypatch: pytest.MonkeyPatch, fail_at: int | None, video_asset_file: Path
) -> None:
    center = Center.objects.create(name="start-audit")
    content_hash = get_file_hash(video_asset_file)
    video = VideoFile.objects.create(
        center=center,
        raw_video_hash=content_hash,
        processed_video_hash=content_hash,
        meta={},
        state=create_media_state(VideoState, anonymized=True),
    )
    with video_asset_file.open("rb") as source:
        video.processed_file.save("start-audit.mp4", File(source))
    started = False
    original_start = AnonymizationService.start

    def observed_start(
        service: AnonymizationService, file_id: int, kind: str | None = None
    ) -> str | None:
        nonlocal started
        assert connection.get_autocommit()
        operation = OperationLog.objects.get(action="anonymization.start_requested")
        assert operation.status_before == operation.status_after
        started = True
        return original_start(service, file_id, kind=kind)

    monkeypatch.setattr(AnonymizationService, "start", observed_start)
    request = APIRequestFactory().post(
        "/anonymization/start/", {"media_type": "video"}, format="json"
    )
    if fail_at is None:
        response = start_anonymization(request, file_id=video.pk)
        assert response.status_code == 200
        assert started
        assert set(OperationLog.objects.values_list("action", flat=True)) == {
            "anonymization.start_requested",
            "anonymization.start",
        }
    else:
        with fail_audit_insert(fail_at=fail_at), pytest.raises(DatabaseError):
            start_anonymization(request, file_id=video.pk)
        assert started == (fail_at == 2)
        assert OperationLog.objects.count() == fail_at - 1


def test_start_rejects_ambient_transaction_before_audit_or_processing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    center = Center.objects.create(name="transactional-start-audit")
    video = VideoFile.objects.create(
        center=center,
        raw_video_hash="transactional-start-audit",
        state=create_media_state(VideoState, anonymized=True),
    )
    started = False
    original_start = AnonymizationService.start

    def observed_start(
        service: AnonymizationService, file_id: int, kind: str | None = None
    ) -> str | None:
        nonlocal started
        started = True
        return original_start(service, file_id, kind=kind)

    monkeypatch.setattr(AnonymizationService, "start", observed_start)
    request = APIRequestFactory().post(
        "/anonymization/start/", {"media_type": "video"}, format="json"
    )
    with transaction.atomic():
        response = start_anonymization(request, file_id=video.pk)
    assert response.status_code == 503
    assert not started
    assert not OperationLog.objects.exists()


def test_video_validation_audit_failure_preserves_raw_bytes_and_reference(
    base_db_data: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    center = Center.objects.create(name="retained-raw-audit")
    user = User.objects.create_user(username="retained-raw-reviewer")
    cast("ManyRelatedManager[Group, Model]", getattr(user, "groups")).add(
        Group.objects.get_or_create(name="data:write")[0]
    )
    PortalUserInfo.objects.create(user=user).centers.add(center)
    video = VideoFile.objects.create(
        center=center,
        processor=get_default_processor(),
        raw_video_hash="retained-raw-audit",
    )
    state = video.get_or_create_state()
    raw_bytes = b"retained raw source"
    video.raw_file.save("retained-raw-audit.mp4", ContentFile(raw_bytes))
    raw_name = video.raw_file.name
    state_before = VideoState.objects.values().get(pk=state.pk)
    cleanup_calls: list[int] = []
    original_cleanup = generation_cleanup.cleanup_validated_raw_video

    def observed_cleanup(video_id: int, *, apply: bool = False) -> tuple[str, ...]:
        cleanup_calls.append(video_id)
        return original_cleanup(video_id, apply=apply)

    monkeypatch.setattr(
        generation_cleanup, "cleanup_validated_raw_video", observed_cleanup
    )
    request = APIRequestFactory().post(
        f"/api/anonymization/{video.pk}/validate/",
        {
            "patient_first_name": "Max",
            "patient_last_name": "Mustermann",
            "patient_dob": "1994-03-21",
            "examination_date": "2024-02-15",
            "casenumber": "audit-case-12345",
            "file_type": "video",
        },
        format="json",
    )
    force_authenticate(request, user=user)
    with fail_audit_insert(), pytest.raises(DatabaseError):
        response = AnonymizationValidateView.as_view()(request, file_id=video.pk)
        pytest.fail(
            f"Expected audit storage failure, got HTTP {response.status_code}: {response.data!r}"
        )
    video.refresh_from_db()
    assert video.raw_file.name == raw_name
    with video.raw_file.open("rb") as raw:
        assert raw.read() == raw_bytes
    assert VideoState.objects.values().get(pk=state.pk) == state_before
    assert cleanup_calls == []
    assert not OperationLog.objects.exists()
