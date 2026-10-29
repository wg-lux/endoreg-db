from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from io import StringIO
from uuid import uuid4

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone
from pydantic import ValidationError

from endoreg_db.models import Center, VideoFile, VideoState
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.models.other.gender import Gender
from endoreg_db.models.state.audit_ledger import AuditLedger
from endoreg_db.schemas.sensitive_meta_verification import (
    SensitiveMetaVerificationCommand,
)
from endoreg_db.services.clinical_lifecycle import (
    ClinicalLifecycleEvent,
    ClinicalLifecycleInvariantError,
    ClinicalLifecycleTransitionError,
    PrivacyStage,
    TransitionRejection,
    derive_clinical_record_state,
    require_clinical_transition,
)
from endoreg_db.services.clinical_lifecycle.facts import video_record_facts
from endoreg_db.services.clinical_lifecycle.legacy_exemption import (
    LEGACY_IDENTITY_EXEMPTION_ACTION,
    has_legacy_identity_exemption,
    legacy_identity_candidates,
    record_legacy_identity_exemptions,
)
from endoreg_db.services.patient_identity.verification import (
    update_sensitive_meta_verification,
)

pytestmark = pytest.mark.django_db

_IMPORTED = timezone.make_aware(datetime(2025, 1, 15))
_CUTOFF = timezone.make_aware(datetime(2026, 1, 1))


def _video(
    *,
    validated: bool = True,
    verified: bool = False,
    imported_at: datetime = _IMPORTED,
) -> VideoFile:
    center = Center.objects.create(name=f"legacy-center-{uuid4().hex[:8]}")
    meta = SensitiveMeta.objects.create(
        center=center,
        patient_first_name="Max",
        patient_last_name="Mustermann",
        patient_dob=timezone.make_aware(datetime(1994, 3, 21)),
        examination_date=date(2025, 1, 10),
        patient_gender=Gender.objects.create(name=f"legacy-{uuid4().hex[:8]}"),
    )
    if verified:
        update_sensitive_meta_verification(
            sensitive_meta_id=meta.pk,
            command=SensitiveMetaVerificationCommand(
                dob_verified=True, names_verified=True
            ),
        )
    state = VideoState.objects.create()
    video = VideoFile.objects.create(
        center=center,
        raw_video_hash=f"legacy-video-{uuid4().hex[:8]}",
        processed_video_hash=uuid4().hex * 2,
        sensitive_meta=meta,
        state=state,
    )
    video.processed_file.name = "videos/processed/legacy.mp4"
    video.save()
    # Written after the artifact exists; date_created is set on insert only.
    VideoState.objects.filter(pk=state.pk).update(
        anonymized=True,
        anonymization_validated=validated,
        date_created=imported_at,
    )
    return VideoFile.objects.get(pk=video.pk)


def _run(*args: str) -> dict[str, object]:
    out = StringIO()
    call_command(
        "exempt_legacy_identity_verification",
        "--cutoff",
        _CUTOFF.isoformat(),
        *args,
        stdout=out,
    )
    return json.loads(out.getvalue())


def test_only_validated_unverified_videos_before_cutoff_are_candidates() -> None:
    legacy = _video()
    _video(verified=True)
    _video(validated=False)
    _video(imported_at=_CUTOFF + timedelta(days=1))

    ids = [c.video_id for c in legacy_identity_candidates(_CUTOFF)]
    assert ids == [legacy.pk]


def test_dry_run_writes_nothing_and_apply_is_idempotent() -> None:
    legacy = _video()

    dry = _run()
    assert dry["candidate_video_ids"] == [legacy.pk]
    assert dry["written"] == 0
    assert not has_legacy_identity_exemption(legacy.pk)

    applied = _run(
        "--apply", "--actor", "ops-admin", "--reason", "Altbestand vor Regel"
    )
    assert applied["written"] == 1
    assert has_legacy_identity_exemption(legacy.pk)

    again = _run("--apply", "--actor", "ops-admin", "--reason", "Altbestand vor Regel")
    assert again["candidate_count"] == 0
    assert again["written"] == 0
    assert (
        AuditLedger.objects.filter(
            object_pk=str(legacy.pk), action=LEGACY_IDENTITY_EXEMPTION_ACTION
        ).count()
        == 1
    )


def test_ledger_payload_contains_no_patient_data() -> None:
    legacy = _video()
    _run("--apply", "--actor", "ops-admin", "--reason", "Altbestand vor Regel")
    row = AuditLedger.objects.get(
        object_pk=str(legacy.pk), action=LEGACY_IDENTITY_EXEMPTION_ACTION
    )
    serialized = json.dumps(row.data)
    assert "Mustermann" not in serialized
    assert "1994" not in serialized
    assert set(row.data) == {
        "schema_version",
        "rule",
        "cutoff",
        "state_created_at",
        "actor",
        "reason",
    }


def test_exempted_video_projects_without_violation_but_still_needs_purge() -> None:
    legacy = _video()
    with pytest.raises(
        ClinicalLifecycleInvariantError, match="validated_without_verified_identity"
    ):
        derive_clinical_record_state(video_record_facts(legacy))

    record_legacy_identity_exemptions(
        legacy_identity_candidates(_CUTOFF),
        cutoff=_CUTOFF,
        actor="ops-admin",
        reason="Altbestand vor Regel",
    )
    facts = video_record_facts(VideoFile.objects.get(pk=legacy.pk))
    assert facts.legacy_identity_exemption
    assert derive_clinical_record_state(facts).privacy is PrivacyStage.VALIDATED
    with pytest.raises(ClinicalLifecycleTransitionError) as exc_info:
        require_clinical_transition(facts, ClinicalLifecycleEvent.EXPORT_APPROVED)
    assert exc_info.value.reason is TransitionRejection.PRIVACY_TRACK_INCOMPLETE


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("--cutoff", "2026-01-01T00:00:00"), "timezone"),
        (("--cutoff", "2999-01-01T00:00:00+00:00"), "future"),
        (("--cutoff", "not-a-date"), "ISO 8601"),
        (("--cutoff", "2026-01-01T00:00:00+00:00", "--apply"), "--actor"),
    ],
)
def test_invalid_command_input_fails_loudly(
    args: tuple[str, ...], message: str
) -> None:
    with pytest.raises(CommandError, match=message):
        call_command("exempt_legacy_identity_verification", *args, stdout=StringIO())


def test_malformed_ledger_payload_fails_loudly() -> None:
    legacy = _video()
    AuditLedger.objects.create(
        object_type="VideoFile",
        object_pk=str(legacy.pk),
        action=LEGACY_IDENTITY_EXEMPTION_ACTION,
        data={"schema_version": "1.0"},
    )
    with pytest.raises(ValidationError):
        has_legacy_identity_exemption(legacy.pk)
