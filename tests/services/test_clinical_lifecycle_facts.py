from __future__ import annotations

from datetime import date, datetime
from uuid import uuid4

import pytest
from django.utils import timezone

from endoreg_db.models import Center, RawPdfFile, VideoFile, VideoState
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.models.other.gender import Gender
from endoreg_db.models.state.raw_pdf import RawPdfState
from endoreg_db.schemas.sensitive_meta_verification import (
    SensitiveMetaVerificationCommand,
)
from endoreg_db.models.aidataset.aidataset import AIDataSet
from endoreg_db.services.clinical_lifecycle import (
    AnnotationStage,
    ClinicalLifecycleEvent,
    ClinicalLifecycleTransitionError,
    ClinicalRecordState,
    ExportApproval,
    PrivacyStage,
    ReleaseStage,
    derive_clinical_record_state,
    require_clinical_transition,
)
from endoreg_db.services.clinical_lifecycle.facts import (
    report_record_facts,
    video_record_facts,
)
from endoreg_db.services.patient_identity.verification import (
    update_sensitive_meta_verification,
)
from endoreg_db.services.privacy.quality_evaluation import (
    clear_sensitive_meta_direct_identifiers,
)

pytestmark = pytest.mark.django_db

_PROCESSED_HASH = "a" * 64


def _center() -> Center:
    return Center.objects.create(name=f"lifecycle-center-{uuid4().hex[:8]}")


def _sensitive_meta(
    center: Center, *, verified: bool, erased: bool = False
) -> SensitiveMeta:
    meta = SensitiveMeta.objects.create(
        center=center,
        patient_first_name="Max",
        patient_last_name="Mustermann",
        patient_dob=timezone.make_aware(datetime(1994, 3, 21)),
        examination_date=date(2025, 11, 27),
        patient_gender=Gender.objects.create(name=f"lifecycle-{uuid4().hex[:8]}"),
    )
    if verified:
        update_sensitive_meta_verification(
            sensitive_meta_id=meta.pk,
            command=SensitiveMetaVerificationCommand(
                dob_verified=True, names_verified=True
            ),
        )
        meta.refresh_from_db()
    if erased:
        clear_sensitive_meta_direct_identifiers(meta)
    return meta


def _video(
    *,
    state: VideoState | None,
    verified: bool,
    erased: bool = False,
    processed: bool = True,
    integrity_lost: bool = False,
    processed_hash_is_default: bool = False,
) -> VideoFile:
    center = _center()
    processed_hash = _PROCESSED_HASH if processed_hash_is_default else uuid4().hex * 2
    video = VideoFile.objects.create(
        center=center,
        raw_video_hash=f"lifecycle-video-{uuid4().hex[:8]}",
        processed_video_hash=processed_hash,
        sensitive_meta=_sensitive_meta(center, verified=verified, erased=erased),
        state=state,
    )
    if processed:
        video.processed_file.name = "videos/processed/lifecycle.mp4"
    if integrity_lost:
        video.meta = {"integrity_status": "lost"}
    video.save()
    return video


def test_video_without_state_is_import_pending() -> None:
    video = _video(state=None, verified=False, processed=False)
    video.state = None
    assert derive_clinical_record_state(video_record_facts(video)) == (
        ClinicalRecordState(
            PrivacyStage.IMPORT_PENDING,
            AnnotationStage.NOT_AVAILABLE,
            ReleaseStage.NOT_RELEASED,
        )
    )


@pytest.mark.parametrize(
    ("erased", "privacy"),
    [(False, PrivacyStage.VALIDATED), (True, PrivacyStage.RAW_PURGED)],
)
def test_validated_video_is_purged_only_after_identifier_erasure(
    erased: bool, privacy: PrivacyStage
) -> None:
    video = _video(
        state=VideoState.objects.create(anonymized=True, anonymization_validated=True),
        verified=True,
        erased=erased,
    )
    assert derive_clinical_record_state(video_record_facts(video)) == (
        ClinicalRecordState(
            privacy, AnnotationStage.IN_PROGRESS, ReleaseStage.NOT_RELEASED
        )
    )


def _approve_after_publication(video: VideoFile, approved_hash: str) -> VideoFile:
    # Publishing a processed artifact invalidates earlier approval, so the
    # reviewed state is written after the artifact exists.
    VideoState.objects.filter(pk=video.state.pk).update(
        anonymized=True,
        anonymization_validated=True,
        segment_annotations_created=True,
        segment_annotations_validated=True,
        outside_segments_removed=True,
        ready_for_export=True,
        ready_for_export_at=timezone.now(),
        ready_for_export_by="lifecycle-reviewer",
        processed_file_sha256=approved_hash,
    )
    return VideoFile.objects.get(pk=video.pk)


@pytest.mark.parametrize(
    ("approved_hash", "approval", "release"),
    [
        (_PROCESSED_HASH, ExportApproval.CURRENT, ReleaseStage.EXPORT_READY),
        ("b" * 64, ExportApproval.STALE, ReleaseStage.NOT_RELEASED),
    ],
)
def test_export_approval_is_bound_to_current_generation(
    approved_hash: str, approval: ExportApproval, release: ReleaseStage
) -> None:
    video = _video(
        state=VideoState.objects.create(),
        verified=True,
        erased=True,
        processed_hash_is_default=True,
    )
    facts = video_record_facts(_approve_after_publication(video, approved_hash))
    assert facts.export_approval is approval
    assert derive_clinical_record_state(facts).release is release


def test_dataset_membership_is_projected_as_ingestion() -> None:
    video = _video(
        state=VideoState.objects.create(),
        verified=True,
        erased=True,
        processed_hash_is_default=True,
    )
    video = _approve_after_publication(video, _PROCESSED_HASH)
    dataset = AIDataSet.objects.create(name=f"lifecycle-{uuid4().hex[:8]}")
    dataset.videos.add(video)
    assert derive_clinical_record_state(video_record_facts(video)).release is (
        ReleaseStage.DATASET_INGESTED
    )


def test_integrity_lost_video_is_lost() -> None:
    video = _video(
        state=VideoState.objects.create(anonymized=True),
        verified=True,
        integrity_lost=True,
    )
    assert derive_clinical_record_state(video_record_facts(video)).privacy is (
        PrivacyStage.LOST
    )


def test_validated_report_cannot_be_released() -> None:
    center = _center()
    report = RawPdfFile.objects.create(
        center=center,
        pdf_hash=f"lifecycle-report-{uuid4().hex}",
        sensitive_meta=_sensitive_meta(center, verified=True, erased=True),
        state=RawPdfState.objects.create(anonymized=True, anonymization_validated=True),
    )
    report.processed_file.name = "reports/processed/lifecycle.pdf"
    report.save()

    facts = report_record_facts(report)
    assert derive_clinical_record_state(facts) == ClinicalRecordState(
        PrivacyStage.RAW_PURGED, AnnotationStage.IN_PROGRESS, ReleaseStage.NOT_RELEASED
    )
    with pytest.raises(ClinicalLifecycleTransitionError):
        require_clinical_transition(facts, ClinicalLifecycleEvent.EXPORT_APPROVED)


def test_audit_command_reports_violations_without_identifiers() -> None:
    import json
    from io import StringIO

    from django.core.management import call_command
    from django.core.management.base import CommandError

    healthy = _video(
        state=VideoState.objects.create(anonymized=True, anonymization_validated=True),
        verified=True,
    )
    broken = _video(
        state=VideoState.objects.create(anonymization_validated=True),
        verified=True,
        processed=False,
    )

    out = StringIO()
    call_command("audit_clinical_lifecycle", "--kind", "video", stdout=out)
    payload = json.loads(out.getvalue())

    assert payload["stage_counts"]["video:validated:in_progress:not_released"] >= 1
    rows = {row["record_id"]: row for row in payload["violations"]}
    assert healthy.pk not in rows
    assert rows[broken.pk]["violations"] == ["validated_without_artifact"]
    assert "Mustermann" not in out.getvalue()

    with pytest.raises(CommandError):
        call_command(
            "audit_clinical_lifecycle",
            "--kind",
            "video",
            "--fail-on-violation",
            stdout=StringIO(),
        )
