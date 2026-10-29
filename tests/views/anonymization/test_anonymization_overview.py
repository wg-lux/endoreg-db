from datetime import timedelta
from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.db.models.fields.files import FieldFile
from django.utils import timezone
from django.test import override_settings
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework import status
from django.core.files.uploadedfile import SimpleUploadedFile  # <--- Import this
import json

# Import your models
from endoreg_db.models import (
    VideoFile,
    RawPdfFile,
    VideoState,
    RawPdfState,
    Center,
    UploadJob,
    VideoHlsArtifact,
    PortalUserInfo,
    SensitiveMeta,
)
from endoreg_db.models.state.anonymization import AnonymizationState
from endoreg_db.models.state.video_segment_validation import SegmentAnnotationStatus
from endoreg_db.views.anonymization.overview import (
    AnonymizationOverviewView,
    UploadJobRetryView,
    anonymization_status,
    start_anonymization,
)
from endoreg_db.services.privacy.workflow import AnonymizationService
from endoreg_db.serializers.misc.file_overview import current_overview_hls_artifacts
from endoreg_db.services.streaming import hls_media as hls_media


@pytest.mark.django_db
@pytest.mark.parametrize(
    "progress",
    ["started", "extracting_frames", "processing_anonymization", "anonymized"],
)
def test_interrupted_video_progress_agrees_between_overview_and_polling(
    progress: str,
) -> None:
    from endoreg_db.services.hub.upload_job_import_lease import (
        reconcile_expired_upload_job_import_leases,
    )

    cache.clear()
    center = Center.objects.create(name="interrupted-progress")
    state = VideoState.objects.create(
        processing_started=progress != "anonymized",
        anonymized=progress == "anonymized",
        was_created=progress == "extracting_frames",
        frames_extracted=progress == "processing_anonymization",
    )
    video = VideoFile.objects.create(
        center=center,
        raw_video_hash="a" * 64,
        processed_file="processed_videos_final/candidate.mp4",
        processed_video_hash="b" * 64,
        state=state,
        meta={},
    )
    job = UploadJob.objects.create(
        source_center=center,
        content_hash=video.raw_video_hash,
        content_type="video/mp4",
        status=UploadJob.Status.PROCESSING,
        processing_lease_owner="terminated-import",
        processing_fencing_token=1,
        processing_heartbeat_at=timezone.now() - timedelta(seconds=2),
        processing_lease_expires_at=timezone.now() - timedelta(seconds=1),
    )
    user = User.objects.create_user(username="interrupted-progress-reader")
    PortalUserInfo.objects.create(user=user).centers.add(center)
    initial = AnonymizationService.get_status(video.pk, kind="video")
    assert initial is not None
    assert initial["anonymization_status"] == progress
    assert reconcile_expired_upload_job_import_leases() == 1
    # A later streaming backfill does not commit the interrupted import.
    VideoHlsArtifact.objects.create(
        video=video,
        artifact_kind="processed",
        status="ready",
        source_file_name=video.processed_file.name,
        source_content_hash=video.processed_video_hash,
        source_generation_id=uuid5(
            NAMESPACE_URL, f"endoreg-db:hls:processed:{video.processed_video_hash}"
        ),
        segment_count=628,
    )
    before = VideoState.objects.values().get(pk=state.pk)

    request = APIRequestFactory().get("/api/anonymization/items/overview/")
    force_authenticate(request, user=user)
    response = AnonymizationOverviewView.as_view(permission_classes=[])(request)
    row = next(row for row in response.data if row["id"] == video.pk)
    assert row["anonymization_status"] == "failed"
    assert row["anonymization_phase"] == "failed"
    assert row["upload_job"]["id"] == str(job.pk)
    assert row["upload_job"]["error_code"] == "import_interrupted"
    assert row["hls_materializations"][0]["status"] == "ready"

    request = APIRequestFactory().get(
        f"/api/anonymization/{video.pk}/status/", {"kind": "video"}
    )
    force_authenticate(request, user=user)
    polled = anonymization_status(request, file_id=video.pk)
    assert polled.status_code == 200
    assert polled.data["anonymization_status"] == row["anonymization_status"]
    assert polled.data["anonymization_phase"] == row["anonymization_phase"]
    assert not polled.data["integrity_status"]
    assert before == VideoState.objects.values().get(pk=state.pk)
    video.refresh_from_db()
    assert video.processed_file.name == "processed_videos_final/candidate.mp4"
    job.refresh_from_db()
    assert job.status == UploadJob.Status.ERROR
    assert not state.anonymization_validated and not state.ready_for_export


@pytest.mark.django_db
@pytest.mark.parametrize(
    "change",
    [
        "center",
        "hash",
        "media_type",
        "live_owner",
        "newer_state",
        "newer_job",
        "completed",
        "validated",
    ],
)
def test_interrupted_upload_does_not_override_unrelated_or_newer_video_state(
    change: str,
) -> None:
    center = Center.objects.create(name="interruption-scope")
    state = VideoState.objects.create(processing_started=True, was_created=False)
    video = VideoFile.objects.create(
        center=center,
        raw_video_hash="a" * 64,
        state=state,
        meta={},
        processed_file="processed_videos_final/retained.mp4",
        processed_video_hash="b" * 64,
    )
    job = UploadJob.objects.create(
        source_center=Center.objects.create(name="other-center")
        if change == "center"
        else center,
        content_hash="c" * 64 if change == "hash" else video.raw_video_hash,
        content_type="application/pdf" if change == "media_type" else "video/mp4",
        status=UploadJob.Status.ERROR,
        error_code=UploadJob.ErrorCode.IMPORT_INTERRUPTED,
        processing_lease_owner="current-owner" if change == "live_owner" else "",
        processing_fencing_token=1 if change == "live_owner" else 0,
        processing_heartbeat_at=timezone.now() if change == "live_owner" else None,
        processing_lease_expires_at=(
            timezone.now() + timedelta(minutes=5) if change == "live_owner" else None
        ),
    )
    if change == "newer_job":
        UploadJob.objects.create(
            source_center=center,
            content_hash=video.raw_video_hash,
            content_type="video/mp4",
            status=UploadJob.Status.PROCESSING,
            processing_lease_owner="new-attempt",
            processing_fencing_token=1,
            processing_heartbeat_at=timezone.now(),
            processing_lease_expires_at=timezone.now() + timedelta(minutes=5),
        )
    if change in {"newer_state", "completed", "validated"}:
        state.sensitive_meta_processed = change in {"completed", "validated"}
        state.anonymized = state.sensitive_meta_processed
        state.anonymization_validated = change == "validated"
        state.save()
        if change != "newer_state":
            job.save(update_fields=["updated_at"])
    expected = state.anonymization_status.value
    user = User.objects.create_user(username="interruption-scope-reader")
    PortalUserInfo.objects.create(user=user).centers.add(center)
    request = APIRequestFactory().get("/api/anonymization/items/overview/")
    force_authenticate(request, user=user)
    response = AnonymizationOverviewView.as_view(permission_classes=[])(request)
    row = next(
        row
        for row in response.data
        if row["id"] == video.pk and row["media_type"] == "video"
    )
    assert row["anonymization_status"] == expected
    polled = AnonymizationService.get_status(video.pk, kind="video")
    assert polled is not None
    assert polled["anonymization_status"] == expected
    job.refresh_from_db()
    assert job.error_code == UploadJob.ErrorCode.IMPORT_INTERRUPTED


@pytest.mark.django_db
@pytest.mark.parametrize(
    "content_type,expected",
    [
        ("application/pdf", "pdf"),
        ("export/txt", "pdf"),
        ("text/plain; charset=utf-8", "pdf"),
        ("text/csv", "pdf"),
        ("video/mp4", "video"),
        ("application/octet-stream", "unknown"),
        ("", "unknown"),
    ],
)
def test_unattached_import_uses_supported_media_identity(
    content_type: str, expected: str
) -> None:
    center = Center.objects.create(name="typed-import")
    UploadJob.objects.create(source_center=center, content_type=content_type)
    user = User.objects.create_user(username="typed-import-reader")
    PortalUserInfo.objects.create(user=user).centers.add(center)
    request = APIRequestFactory().get("/api/anonymization/items/overview/")
    force_authenticate(request, user=user)
    response = AnonymizationOverviewView.as_view(permission_classes=[])(request)
    assert response.status_code == 200
    rows = response.data
    assert len(rows) == 1
    assert rows[0]["media_type"] == expected


@pytest.mark.django_db
def test_shared_metadata_never_overrides_document_hash_or_media_type() -> None:
    from endoreg_db.import_files.context.default_sensitive_meta import (
        default_sensitive_meta,
    )

    center = Center.objects.create(name="shared-overview")
    report = RawPdfFile.objects.create(center=center, pdf_hash="a" * 64)
    meta = default_sensitive_meta(report)
    assert meta is not None
    video = VideoFile.objects.create(
        center=center, sensitive_meta=meta, raw_video_hash="b" * 64
    )
    matching_report = UploadJob.objects.create(
        source_center=center,
        sensitive_meta=meta,
        content_hash=report.pdf_hash,
        content_type="export/txt",
        original_filename="correct.txt",
    )
    matching_video = UploadJob.objects.create(
        source_center=center,
        sensitive_meta=meta,
        content_hash=video.raw_video_hash,
        content_type="video/mp4",
        original_filename="correct.mp4",
    )
    UploadJob.objects.create(
        source_center=center,
        sensitive_meta=meta,
        content_hash="c" * 64,
        content_type="application/pdf",
        original_filename="different.pdf",
    )
    user = User.objects.create_user(username="shared-overview-reader")
    PortalUserInfo.objects.create(user=user).centers.add(center)
    items = AnonymizationOverviewView().get_queryset(request_user=user)
    attached = {
        ("video" if isinstance(item, VideoFile) else "pdf", item.pk): getattr(
            item, "_overview_upload_job", None
        )
        for item in items
    }
    assert attached[("pdf", report.pk)] == matching_report
    assert attached[("video", video.pk)] == matching_video
    RawPdfFile.objects.filter(pk=report.pk).update(pdf_hash="d" * 64)
    report_item = next(
        item
        for item in AnonymizationOverviewView().get_queryset(request_user=user)
        if isinstance(item, RawPdfFile)
    )
    assert getattr(report_item, "_overview_upload_job", None) is None


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["pdf", "report", "video", None, "invalid"])
def test_start_disambiguates_shared_ids_and_preserves_har_integrity_gate(
    kind: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    center = Center.objects.create(name="start-identity")
    VideoFile.objects.create(
        id=15,
        center=center,
        raw_video_hash="start-video",
        meta={"integrity_status": "lost"},
        state=VideoState.objects.create(),
    )
    RawPdfFile.objects.create(
        id=15,
        center=center,
        pdf_hash="start-report",
        state=RawPdfState.objects.create(),
    )
    calls: list[tuple[int, str | None]] = []

    def start(
        _self: AnonymizationService, file_id: int, kind: str | None = None
    ) -> str:
        calls.append((file_id, kind))
        return "pdf"

    monkeypatch.setattr(AnonymizationService, "start", start)
    user = User.objects.create_user(username="start-reader")
    request = APIRequestFactory().post(
        "/api/anonymization/15/start/",
        {} if kind is None else {"media_type": kind},
        format="json",
    )
    force_authenticate(request, user=user)
    response = start_anonymization(request, file_id=15)
    if kind in {"pdf", "report"}:
        assert response.status_code == 200
        assert calls == [(15, "pdf")]
        assert response.data["file_type"] == "pdf"
    elif kind == "video":
        assert response.status_code == 409
        assert response.data["integrity_status"] == "lost"
        assert not calls
    else:
        assert response.status_code == 400
        assert not calls


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["pdf", "report", "video"])
def test_status_honors_media_identity_with_colliding_ids(kind: str) -> None:
    cache.clear()
    center = Center.objects.create(name="status-identity")
    VideoFile.objects.create(
        id=15, center=center, raw_video_hash="status-video", meta={}
    )
    RawPdfFile.objects.create(id=15, center=center, pdf_hash="status-report")
    user = User.objects.create_user(username="status-reader")
    request = APIRequestFactory().get("/api/anonymization/15/status/", {"kind": kind})
    force_authenticate(request, user=user)
    response = anonymization_status(request, file_id=15)
    assert response.status_code == 200
    assert response.data["file_type"] == ("video" if kind == "video" else "pdf")
    assert response.data["anonymization_phase"] == "not_started"


@pytest.mark.django_db
@pytest.mark.parametrize("integrity_status", ["", "lost"])
def test_processing_failure_is_distinct_from_durable_integrity_loss(
    integrity_status: str,
) -> None:
    center = Center.objects.create(name="failure-identity")
    video = VideoFile.objects.create(
        center=center,
        raw_video_hash="failure-source",
        meta={"integrity_status": integrity_status} if integrity_status else {},
        state=VideoState.objects.create(processing_error=True),
    )
    payload = AnonymizationService.get_status(video.pk, kind="video")
    assert payload is not None
    assert payload["anonymization_status"] == "failed"
    assert (payload.get("integrity_status") or "") == integrity_status


@pytest.mark.parametrize("replacement_status", ["failed", "queued", "materializing"])
@pytest.mark.parametrize("reverse_order", [False, True])
def test_overview_prefers_current_publication_over_replacement(
    replacement_status: str, reverse_order: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Overview must not hash, decode, scan or dispatch media")

    monkeypatch.setattr(hls_media, "_source_content_hash", forbidden)
    monkeypatch.setattr(hls_media, "_persisted_hls_boundaries", forbidden)
    monkeypatch.setattr(hls_media, "_ready_artifact_paths_exist", forbidden)
    monkeypatch.setattr(hls_media, "dispatch_video_hls_materialization", forbidden)
    video = VideoFile(
        processed_file="processed/current.mp4", processed_video_hash="a" * 64
    )
    published = VideoHlsArtifact(
        id=1,
        video=video,
        artifact_kind="processed",
        status="ready",
        source_content_hash="a" * 64,
        source_file_name=video.processed_file.name,
        source_generation_id=uuid5(
            NAMESPACE_URL, f"endoreg-db:hls:processed:{'a' * 64}"
        ),
        updated_at=timezone.now() - timedelta(days=1),
    )
    replacement = VideoHlsArtifact(
        id=2,
        video=video,
        artifact_kind="processed",
        status=replacement_status,
        updated_at=timezone.now(),
    )
    artifacts = [published, replacement]
    if reverse_order:
        artifacts.reverse()
    assert current_overview_hls_artifacts(artifacts, video=video) == [published]


@pytest.mark.parametrize(
    "changed", ["hash", "filename", "generation", "profile", "missing_hash"]
)
def test_overview_rejects_stale_or_unknown_publication(changed: str) -> None:
    video = VideoFile(
        processed_file="processed/current.mp4", processed_video_hash="a" * 64
    )
    published = VideoHlsArtifact(
        id=1,
        video=video,
        artifact_kind="processed",
        status="ready",
        source_content_hash="a" * 64,
        source_file_name=video.processed_file.name,
        source_generation_id=uuid5(
            NAMESPACE_URL, f"endoreg-db:hls:processed:{'a' * 64}"
        ),
        updated_at=timezone.now(),
    )
    if changed == "hash":
        video.processed_video_hash = "b" * 64
    elif changed == "filename":
        video.processed_file.name = "processed/replacement.mp4"
    elif changed == "generation":
        published.source_generation_id = uuid5(NAMESPACE_URL, "another-generation")
    elif changed == "profile":
        published.encoding_profile_name = "unknown-profile"
    else:
        video.processed_video_hash = ""
    assert current_overview_hls_artifacts([published], video=video) == []
    failed = VideoHlsArtifact(
        id=2,
        video=video,
        artifact_kind="processed",
        status="failed",
        updated_at=timezone.now() - timedelta(days=1),
        error_code="validation_failed",
    )
    assert current_overview_hls_artifacts([failed, published], video=video) == [failed]


@pytest.mark.parametrize("validated", [False, True])
@pytest.mark.parametrize("processed_status", ["ready", "failed", "stale", "absent"])
@pytest.mark.parametrize("raw_status", ["failed", "queued", "materializing"])
def test_overview_retires_only_historical_raw_failure_after_validation(
    validated: bool, processed_status: str, raw_status: str
) -> None:
    video = VideoFile(
        raw_file="raw/legacy-reference.mp4",
        processed_file="processed/current.mp4",
        processed_video_hash="a" * 64,
    )
    video.state = VideoState(anonymization_validated=validated)
    raw = VideoHlsArtifact(
        id=1,
        video=video,
        artifact_kind="raw",
        status=raw_status,
        updated_at=timezone.now(),
    )
    processed = VideoHlsArtifact(
        id=2,
        video=video,
        artifact_kind="processed",
        status="ready" if processed_status == "stale" else processed_status,
        source_content_hash="b" * 64 if processed_status == "stale" else "a" * 64,
        source_file_name=video.processed_file.name,
        source_generation_id=uuid5(
            NAMESPACE_URL, f"endoreg-db:hls:processed:{'a' * 64}"
        ),
        updated_at=timezone.now(),
    )
    artifacts = [raw] if processed_status == "absent" else [raw, processed]
    selected = current_overview_hls_artifacts(artifacts, video=video)
    hidden = validated and processed_status == "ready" and raw_status == "failed"
    assert (raw not in selected) == hidden
    assert (processed in selected) == (processed_status in {"ready", "failed"})
    assert raw.status == raw_status


def test_overview_does_not_substitute_ready_raw_for_failed_processed() -> None:
    video = VideoFile(raw_file="raw/current.mp4", raw_video_hash="a" * 64)
    raw = VideoHlsArtifact(
        id=1,
        video=video,
        artifact_kind="raw",
        status="ready",
        source_content_hash="a" * 64,
        source_file_name=video.raw_file.name,
        source_generation_id=uuid5(NAMESPACE_URL, f"endoreg-db:hls:raw:{'a' * 64}"),
        updated_at=timezone.now(),
    )
    failed = VideoHlsArtifact(
        id=2,
        video=video,
        artifact_kind="processed",
        status="failed",
        updated_at=timezone.now(),
        error_code="validation_failed",
    )
    assert current_overview_hls_artifacts([raw, failed], video=video) == [failed, raw]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "deployment_role",
    ("standalone", "site_node", "local_study_server"),
)
def test_centerless_overview_returns_explicit_forbidden(
    deployment_role: str,
) -> None:
    request = APIRequestFactory().get("/api/anonymization/items/overview/")
    user = User.objects.create_user(username=f"centerless-{deployment_role}")
    force_authenticate(request, user=user)

    with override_settings(ENDOREG_DEPLOYMENT_ROLE=deployment_role):
        response = AnonymizationOverviewView.as_view(permission_classes=[])(request)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "No center membership" in str(response.data["detail"])


@pytest.mark.django_db
def test_anonymization_overview_mixed_content():
    factory = APIRequestFactory()

    # 1. Setup Data
    center = Center.objects.create(name="Test Center")

    # --- Create Video (Status: VALIDATED) ---
    video_state = VideoState.objects.create(
        anonymization_validated=True,
        anonymized=True,
        sensitive_meta_processed=True,
        frames_extracted=True,
    )

    # Create a dummy video file in memory
    dummy_video = SimpleUploadedFile(
        "old_video.mp4", b"dummy_content", content_type="video/mp4"
    )

    video = VideoFile.objects.create(
        center=center,
        raw_video_hash="hash123",
        original_file_name="tmpabc123.mp4",
        raw_file=dummy_video,  # <--- Pass the file object, not a string
        state=video_state,
    )
    UploadJob.objects.create(
        file="upload_jobs/video_upload.mp4",
        status=UploadJob.Status.ANONYMIZED,
        content_type="video/mp4",
        source_center=center,
        source_system="watcher-daemon",
        content_hash="hash123",
        ingest_mode=UploadJob.IngestMode.WATCHER,
        original_filename="/incoming/old_video.mp4",
        source_file_persisted=False,
        cleanup_status=UploadJob.CleanupStatus.COMPLETED,
        idempotency_key="do-not-expose",
        processing_provenance={"stored_upload_path": "/protected/raw/old_video.mp4"},
    )
    # Force older date
    VideoFile.objects.filter(pk=video.pk).update(
        uploaded_at=timezone.now() - timedelta(days=1)
    )

    # --- Create PDF (Status: DONE_PROCESSING -> Needs Validation) ---
    pdf_state = RawPdfState.objects.create(
        sensitive_meta_processed=True, anonymization_validated=False
    )

    # Create a dummy PDF file in memory
    dummy_pdf = SimpleUploadedFile(
        "new_report.pdf", b"dummy_pdf_content", content_type="application/pdf"
    )

    pdf = RawPdfFile.objects.create(
        center=center,
        pdf_hash="hash456",
        file=dummy_pdf,  # <--- Pass the file object, not a string
        state=pdf_state,
    )
    UploadJob.objects.create(
        file="upload_jobs/pdf_upload.pdf",
        status=UploadJob.Status.ERROR,
        content_type="application/pdf",
        source_center=center,
        source_system="api-client",
        content_hash="hash456",
        ingest_mode=UploadJob.IngestMode.API,
        original_filename="C:\\incoming\\new_report.pdf",
        source_file_persisted=True,
        cleanup_status=UploadJob.CleanupStatus.PENDING,
        error_detail="OCR failed while parsing /protected/raw/new_report.pdf",
        error_code=UploadJob.ErrorCode.PROCESSING_FAILED,
    )
    VideoHlsArtifact.objects.create(
        video=video,
        artifact_kind=VideoHlsArtifact.ArtifactKind.RAW,
        status=VideoHlsArtifact.Status.READY,
        segment_count=2,
        source_content_hash=video.raw_video_hash,
        source_file_name=video.raw_file.name,
        source_generation_id=uuid5(
            NAMESPACE_URL, f"endoreg-db:hls:raw:{video.raw_video_hash}"
        ),
    )
    VideoHlsArtifact.objects.create(
        video=video,
        artifact_kind=VideoHlsArtifact.ArtifactKind.RAW,
        status=VideoHlsArtifact.Status.SUPERSEDED,
        segment_count=1,
    )
    VideoHlsArtifact.objects.create(
        video=video,
        artifact_kind=VideoHlsArtifact.ArtifactKind.PROCESSED,
        status=VideoHlsArtifact.Status.VALIDATED,
        segment_count=2,
    )

    # 2. Execute Request
    url = "/api/anonymization/items/overview/"
    request = factory.get(url)
    user = User.objects.create_user(username="overview-reader")
    portal_info = PortalUserInfo.objects.create(user=user)
    portal_info.centers.add(center)
    force_authenticate(request, user=user)
    view = AnonymizationOverviewView.as_view(permission_classes=[])
    response = view(request)

    # 3. Assertions
    assert response.status_code == status.HTTP_200_OK
    data = json.loads(response.content)

    assert len(data) == 2

    # Verify PDF (Newer)
    assert data[0]["media_type"] == "pdf"
    assert data[0]["id"] == pdf.pk
    assert data[0]["file_size"] > 0  # Check that size works now
    assert data[0]["upload_job"]["status"] == UploadJob.Status.ERROR
    assert data[0]["upload_job"]["ingest_mode"] == UploadJob.IngestMode.API
    assert data[0]["upload_job"]["original_filename"] == "new_report.pdf"
    assert data[0]["upload_job"]["source_file_persisted"] is True
    assert data[0]["upload_job"]["error_code"] == "processing_failed"
    assert data[0]["upload_job"]["allowed_actions"] == ["safe_reimport", "delete"]
    assert data[0]["upload_job"]["error_detail"] == (
        "Import processing failed. Technical details are available in protected logs."
    )
    assert "/protected" not in data[0]["upload_job"]["error_detail"]

    # Verify Video (Older)
    assert data[1]["media_type"] == "video"
    assert data[1]["id"] == video.pk
    assert data[1]["filename"] == f"video-{video.pk}"
    assert data[1]["upload_job"]["status"] == UploadJob.Status.ANONYMIZED
    assert data[1]["upload_job"]["ingest_mode"] == UploadJob.IngestMode.WATCHER
    assert data[1]["upload_job"]["source_system"] == "watcher-daemon"
    assert data[1]["upload_job"]["source_center_key"] == center.center_key
    assert data[1]["upload_job"]["original_filename"] == "old_video.mp4"
    assert data[1]["upload_job"]["source_file_persisted"] is False
    assert data[1]["upload_job"]["cleanup_status"] == UploadJob.CleanupStatus.COMPLETED
    assert data[1]["upload_job"]["allowed_actions"] == ["delete"]
    assert "processing_provenance" not in data[1]["upload_job"]
    assert "idempotency_key" not in data[1]["upload_job"]
    assert "content_hash" not in data[1]["upload_job"]
    assert "file" not in data[1]["upload_job"]
    materializations = {
        item["artifact_kind"]: item for item in data[1]["hls_materializations"]
    }
    assert set(materializations) == {"raw", "processed"}
    assert materializations["raw"]["status"] == "ready"
    assert materializations["raw"]["segment_count"] == 2
    assert materializations["raw"]["source_generation_id"]
    assert materializations["raw"]["target_generation_id"]
    assert materializations["processed"]["status"] == "materializing"

    # Verify Statuses
    assert (
        data[0]["anonymization_status"]
        == AnonymizationState.DONE_PROCESSING_ANONYMIZATION
    )
    assert data[0]["annotation_status"] == "not_started"

    assert data[1]["anonymization_status"] == AnonymizationState.VALIDATED
    assert data[1]["annotation_status"] == SegmentAnnotationStatus.NOT_STARTED.value
    assert data[0]["anonymization_phase"] == "awaiting_validation"
    assert data[1]["anonymization_phase"] == "validated"
    # AI predictions exist only for videos; frames exist, prediction has not run yet.
    assert data[0]["ai_prediction_status"] is None
    assert data[1]["ai_prediction_status"] == "prediction_pending"


@pytest.mark.django_db
def test_overview_includes_storage_blocked_upload_without_video_file() -> None:
    center = Center.objects.create(name="Retry Center")
    user = User.objects.create_user(username="storage-retry-reader")
    portal_info = PortalUserInfo.objects.create(user=user)
    portal_info.centers.add(center)
    upload_job = UploadJob.objects.create(
        file=SimpleUploadedFile(
            "storage-blocked.mp4",
            b"managed-source",
            content_type="video/mp4",
        ),
        content_type="video/mp4",
        source_center=center,
        source_system="watcher-daemon",
        ingest_mode=UploadJob.IngestMode.WATCHER,
        original_filename="/input/storage-blocked.mp4",
    )
    upload_job.schedule_retry(
        "Insufficient pipeline storage. Required: 11.1 GB, Available: 2.8 GB",
        error_code=UploadJob.ErrorCode.PROCESSING_FAILED,
        delay_seconds=30,
        max_retries=96,
    )

    request = APIRequestFactory().get("/api/anonymization/items/overview/")
    force_authenticate(request, user=user)
    response = AnonymizationOverviewView.as_view(permission_classes=[])(request)

    assert response.status_code == status.HTTP_200_OK
    payload = json.loads(response.content)
    assert len(payload) == 1
    row = payload[0]
    assert row["import_only"] is True
    assert row["filename"] == "storage-blocked.mp4"
    assert row["media_type"] == "video"
    assert row["upload_job"]["id"] == str(upload_job.pk)
    assert row["upload_job"]["status"] == "retrying"
    assert row["upload_job"]["retryable"] is True
    assert row["upload_job"]["error_detail"] == (
        "Import processing failed. Technical details are available in protected logs."
    )
    assert "/input/" not in json.dumps(row)


@pytest.mark.django_db
def test_unattached_upload_stays_visible_through_recovery() -> None:
    center = Center.objects.create(name="Lifecycle Center")
    user = User.objects.create_user(username="lifecycle-reader")
    PortalUserInfo.objects.create(user=user).centers.add(center)
    job = UploadJob.objects.create(source_center=center, content_type="video/mp4")
    stable_id: int | None = None
    for job_status in (
        UploadJob.Status.PENDING,
        UploadJob.Status.PROCESSING,
        UploadJob.Status.RETRYING,
        UploadJob.Status.ERROR,
        UploadJob.Status.LOST,
    ):
        if job_status == UploadJob.Status.RETRYING:
            job.schedule_retry(
                "Processing interrupted",
                delay_seconds=30,
                max_retries=3,
                error_code=UploadJob.ErrorCode.PROCESSING_FAILED,
            )
        elif job_status == UploadJob.Status.ERROR:
            job.mark_error("Retry budget exhausted")
        elif job_status == UploadJob.Status.LOST:
            job.mark_lost("Source unavailable")
        elif job_status == UploadJob.Status.PROCESSING:
            job.mark_processing()
        request = APIRequestFactory().get("/api/anonymization/items/overview/")
        force_authenticate(request, user=user)
        response = AnonymizationOverviewView.as_view(permission_classes=[])(request)
        rows = json.loads(response.content)
        assert len(rows) == 1
        row = rows[0]
        assert row["upload_job"]["id"] == str(job.pk)
        assert row["upload_job"]["status"] == job_status
        assert row["anonymization_status"] == (
            "failed"
            if job_status in (UploadJob.Status.ERROR, UploadJob.Status.LOST)
            else "processing_anonymization"
        )
        assert row["anonymization_phase"] == (
            "failed"
            if job_status in (UploadJob.Status.ERROR, UploadJob.Status.LOST)
            else "in_progress"
        )
        assert row["ai_prediction_status"] is None
        assert row["import_only"] is True
        if job_status in (UploadJob.Status.ERROR, UploadJob.Status.LOST):
            assert "safe_reimport" not in row["upload_job"]["allowed_actions"]
        if stable_id is None:
            stable_id = row["id"]
        assert row["id"] == stable_id


@pytest.mark.django_db
def test_overview_never_attaches_foreign_center_upload_with_same_hash() -> None:
    own = Center.objects.create(name="Attachment Own")
    foreign = Center.objects.create(name="Attachment Foreign")
    user = User.objects.create_user(username="attachment-reader")
    PortalUserInfo.objects.create(user=user).centers.add(own)
    video = VideoFile.objects.create(center=own, raw_video_hash="shared-content")
    own_job = UploadJob.objects.create(
        source_center=own, content_hash=video.raw_video_hash, content_type="video/mp4"
    )
    foreign_job = UploadJob.objects.create(
        source_center=foreign,
        content_hash=video.raw_video_hash,
        content_type="video/mp4",
    )
    request = APIRequestFactory().get("/api/anonymization/items/overview/")
    force_authenticate(request, user=user)
    response = AnonymizationOverviewView.as_view(permission_classes=[])(request)
    rows = json.loads(response.content)
    assert len(rows) == 1
    assert rows[0]["upload_job"]["id"] == str(own_job.pk)
    assert str(foreign_job.pk) not in json.dumps(rows)


@pytest.mark.django_db
def test_retry_view_recovers_legacy_terminal_storage_failure() -> None:
    center = Center.objects.create(name="Retry Center")
    user = User.objects.create_user(username="storage-retry-operator")
    portal_info = PortalUserInfo.objects.create(user=user)
    portal_info.centers.add(center)
    upload_job = UploadJob.objects.create(
        file=SimpleUploadedFile(
            "retained.mp4",
            b"managed-source",
            content_type="video/mp4",
        ),
        content_type="video/mp4",
        source_center=center,
        original_filename="retained.mp4",
        status=UploadJob.Status.ERROR,
        error_code=UploadJob.ErrorCode.PROCESSING_FAILED,
        error_detail=(
            "Insufficient pipeline storage. Required: 11.1 GB, Available: 2.8 GB"
        ),
    )
    request = APIRequestFactory().post(
        f"/api/anonymization/upload-jobs/{upload_job.pk}/retry/"
    )
    force_authenticate(request, user=user)

    response = UploadJobRetryView.as_view(permission_classes=[])(
        request,
        job_id=upload_job.pk,
    )

    assert response.status_code == status.HTTP_202_ACCEPTED
    upload_job.refresh_from_db()
    assert upload_job.status == UploadJob.Status.RETRYING
    assert upload_job.retryable is True
    assert upload_job.retry_count == 1
    assert upload_job.max_retries == 96
    assert upload_job.next_retry_at is not None
    assert upload_job.next_retry_at <= timezone.now()


@pytest.mark.django_db
@override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
def test_central_hub_overview_unscopes_videos_but_not_reports() -> None:
    local = Center.objects.create(name="Local", center_key="local")
    foreign = Center.objects.create(name="Foreign", center_key="foreign")
    local_video = VideoFile.objects.create(center=local, raw_video_hash="local-video")
    foreign_video = VideoFile.objects.create(
        center=foreign,
        raw_video_hash="foreign-video",
        original_file_name="patient-name-foreign.mp4",
    )
    cast(Any, foreign_video.processed_file).save(
        "foreign-anonymized.mp4", ContentFile(b"processed"), save=True
    )
    foreign_state = foreign_video.get_or_create_state()
    foreign_state.anonymized = True
    foreign_state.save(update_fields=["anonymized"])
    local_pdf = RawPdfFile.objects.create(center=local, pdf_hash="local-pdf")
    foreign_pdf = RawPdfFile.objects.create(center=foreign, pdf_hash="foreign-pdf")
    user = User.objects.create_user(username="hub-overview-reader")
    portal_info = PortalUserInfo.objects.create(user=user)
    portal_info.centers.add(local)

    items = AnonymizationOverviewView().get_queryset(request_user=user)

    assert {item.pk for item in items if isinstance(item, VideoFile)} == {
        local_video.pk,
        foreign_video.pk,
    }
    assert {item.pk for item in items if isinstance(item, RawPdfFile)} == {local_pdf.pk}
    assert foreign_pdf not in items

    request = APIRequestFactory().get("/api/anonymization/items/overview/")
    force_authenticate(request, user=user)
    response = AnonymizationOverviewView.as_view(permission_classes=[])(request)
    payload = json.loads(response.content)
    foreign_payload = next(
        item
        for item in payload
        if item["media_type"] == "video" and item["id"] == foreign_video.pk
    )
    assert foreign_payload["filename"] == f"Video {foreign_video.pk}"
    assert foreign_payload["sensitive_meta_id"] is None
    assert "patient_hash_display" not in foreign_payload
    assert foreign_payload["pseudo_patient_id"] is None
    assert foreign_payload["upload_job"] is None


@pytest.mark.django_db
@override_settings(ENDOREG_DEPLOYMENT_ROLE="central_hub")
def test_central_hub_centerless_user_discovers_processed_videos_only() -> None:
    center = Center.objects.create(name="Foreign", center_key="foreign")
    video = VideoFile.objects.create(
        center=center, raw_video_hash="centerless-hub-video"
    )
    cast(Any, video.processed_file).save(
        "centerless-hub-anonymized.mp4", ContentFile(b"processed"), save=True
    )
    state = video.get_or_create_state()
    state.anonymized = True
    state.save(update_fields=["anonymized"])
    report = RawPdfFile.objects.create(center=center, pdf_hash="centerless-hub-report")
    user = User.objects.create_user(username="centerless-hub-overview-reader")

    items = AnonymizationOverviewView().get_queryset(request_user=user)

    assert video in items
    assert report not in items


@pytest.mark.django_db
@pytest.mark.parametrize("media_type", ["video", "pdf"])
@pytest.mark.parametrize("completed", [False, True])
@pytest.mark.parametrize("job_status", ["error", "lost", "processing"])
def test_failed_duplicates_do_not_overlay_completed_media(
    media_type: str, completed: bool, job_status: str
) -> None:
    center = Center.objects.create(name="duplicate-overview")
    source_hash = "a" * 64
    if media_type == "video":
        item = VideoFile.objects.create(
            center=center,
            raw_video_hash=source_hash,
            processed_file="processed/video.mp4",
            processed_video_hash="b" * 64,
            state=VideoState.objects.create(
                anonymized=completed, sensitive_meta_processed=completed
            ),
        )
    else:
        item = RawPdfFile.objects.create(
            center=center,
            pdf_hash=source_hash,
            processed_file="processed/report.pdf",
            state=RawPdfState.objects.create(
                anonymized=completed,
                sensitive_meta_processed=completed,
                processed_file_sha256="b" * 64,
            ),
        )
    if isinstance(item, RawPdfFile):
        from endoreg_db.import_files.context.default_sensitive_meta import (
            default_sensitive_meta,
        )

        meta = default_sensitive_meta(item)
        assert meta is not None
        SensitiveMeta.objects.filter(pk=meta.pk).update(
            patient_hash="c" * 64, examination_hash="d" * 64
        )
        item.anonymized_text = "Retained anonymized report"
        item.processed_file.name = ""
        item.save(update_fields=["anonymized_text", "processed_file"])
    jobs = [
        UploadJob.objects.create(
            source_center=center,
            content_hash=source_hash,
            status=job_status,
            error_code="" if job_status == "processing" else "processing_failed",
            content_type="video/mp4" if media_type == "video" else "application/pdf",
        )
        for _ in range(1 if job_status == "processing" else 2)
    ]
    user = User.objects.create_user(username="duplicate-reader")
    PortalUserInfo.objects.create(user=user).centers.add(center)
    request = APIRequestFactory().get("/api/anonymization/items/overview/")
    force_authenticate(request, user=user)
    response = AnonymizationOverviewView.as_view(permission_classes=[])(request)
    assert response.status_code == 200
    rows = json.loads(response.content)
    media_row = next(row for row in rows if row["id"] == item.pk)
    superseded = completed and job_status in {"error", "lost"}
    assert (media_row["upload_job"] is None) is superseded
    assert len(rows) == (1 if superseded else len(jobs))
    assert UploadJob.objects.filter(pk__in=[job.pk for job in jobs]).count() == len(
        jobs
    )


@pytest.mark.django_db
def test_start_reset_video_reprocesses_existing_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import Mock
    from endoreg_db.services.privacy import workflow

    def file_exists(field: FieldFile) -> bool:
        return bool(field.name)

    video = VideoFile.objects.create(
        raw_video_hash="reset-existing-video",
        center=Center.objects.create(name="reset-existing-video"),
        raw_file="retained-source.mp4",
        meta={},
        state=VideoState.objects.create(was_created=False),
    )
    importer = Mock(spec=workflow.VideoImportService)
    monkeypatch.setattr(workflow, "VideoImportService", lambda: importer)
    monkeypatch.setattr(workflow, "file_exists", file_exists)
    service = AnonymizationService()

    assert service.start(video.pk, kind="video") == "video"
    importer.reanonymize_existing_video.assert_called_once()
    assert importer.reanonymize_existing_video.call_args.args[0].pk == video.pk
    importer.import_and_anonymize.assert_not_called()
    assert VideoFile.objects.filter(raw_video_hash=video.raw_video_hash).count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize("locked", [False, True])
@pytest.mark.parametrize("processing", [False, True])
@pytest.mark.parametrize("media_type", ["video", "pdf"])
def test_polling_reports_actual_lock(
    media_type: str, processing: bool, locked: bool
) -> None:
    from endoreg_db.services.runtime.polling_coordinator import PollingCoordinator

    cache.clear()
    center = Center.objects.create(name="polling-lock")
    if media_type == "video":
        media = VideoFile.objects.create(
            center=center,
            raw_video_hash="polling-lock-video",
            state=VideoState.objects.create(frames_extracted=processing),
        )
    else:
        media = RawPdfFile.objects.create(
            center=center,
            pdf_hash="polling-lock-report",
            state=RawPdfState.objects.create(processing_started=processing),
        )
    if locked:
        assert PollingCoordinator.acquire_processing_lock(media.pk, media_type)
    try:
        user = User.objects.create_user(username="polling-lock-reader")
        request = APIRequestFactory().get(
            f"/api/anonymization/{media.pk}/status/", {"kind": media_type}
        )
        force_authenticate(request, user=user)
        response = anonymization_status(request, file_id=media.pk)
        assert response.status_code == 200
        assert response.data["processing_locked"] is locked
        assert media.state is not None
        assert (
            response.data["anonymization_status"]
            == media.state.anonymization_status.value
        )
    finally:
        if locked:
            PollingCoordinator.release_processing_lock(media.pk, media_type)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        ({}, "frames_unavailable"),
        ({"frames_extracted": True}, "prediction_pending"),
        (
            {"frames_extracted": True, "initial_prediction_completed": True},
            "prediction_ready",
        ),
        (
            {
                "frames_extracted": True,
                "initial_prediction_completed": True,
                "lvs_created": True,
            },
            "annotation_ready",
        ),
    ],
)
def test_overview_sends_native_ai_prediction_status_without_extra_queries(
    flags: dict[str, bool], expected: str
) -> None:
    center = Center.objects.create(name=f"prediction-{expected}")
    user = User.objects.create_superuser(username=f"prediction-{expected}")

    def overview() -> list[dict[str, object]]:
        request = APIRequestFactory().get("/api/anonymization/items/overview/")
        force_authenticate(request, user=user)
        response = AnonymizationOverviewView.as_view(permission_classes=[])(request)
        return json.loads(response.content)

    for index in range(3):
        VideoFile.objects.create(
            center=center,
            raw_video_hash=f"prediction-{expected}-{index}",
            meta={},
            state=VideoState.objects.create(**flags),
        )
    with CaptureQueriesContext(connection) as queries:
        rows = overview()
    assert [row["ai_prediction_status"] for row in rows] == [expected] * 3
    # Prediction flags come from the joined state row, never a per-row lookup.
    state_table = VideoState._meta.db_table
    assert not [
        query["sql"]
        for query in queries.captured_queries
        if query["sql"].startswith(f'SELECT "{state_table}"')
    ]
