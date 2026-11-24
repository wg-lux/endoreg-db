"""Named exception for videos validated before identity verification was required.

Video validation historically did not require a verified first name, last name
and date of birth. Backfilling the verification flags would fabricate evidence
of a human check that never happened. Instead an operator records one immutable,
hash-chained audit-ledger entry per eligible video. The exemption:

* only replaces the identity evidence of an already validated legacy video;
* never authorizes a new ``anonymization_validated`` transition;
* never waives the raw-media and direct-identifier purge before export.

The stored payload contains no patient data. Approved by the maintainer on
2026-10-22 as the handling for legacy videos.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal

from django.db import transaction
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field, field_validator

from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.state.audit_ledger import AuditLedger

LEGACY_IDENTITY_EXEMPTION_ACTION: Final = "legacy_identity_exemption"
_OBJECT_TYPE: Final = "VideoFile"


class LegacyIdentityExemptionPayload(BaseModel):
    """Persisted audit-ledger payload; validated on every write and read."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["1.0"] = "1.0"
    rule: Literal["clinical_lifecycle_identity_v1"] = "clinical_lifecycle_identity_v1"
    cutoff: datetime
    state_created_at: datetime
    actor: str = Field(min_length=1, max_length=150)
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("cutoff", "state_created_at")
    @classmethod
    def _require_aware(cls, value: datetime) -> datetime:
        if timezone.is_naive(value):
            raise ValueError("timestamps must be timezone-aware")
        return value


@dataclass(frozen=True, slots=True)
class LegacyIdentityCandidate:
    video_id: int
    state_created_at: datetime


def _require_past_cutoff(cutoff: datetime) -> None:
    if timezone.is_naive(cutoff):
        raise ValueError("cutoff must be timezone-aware")
    if cutoff > timezone.now():
        raise ValueError("cutoff must not lie in the future")


def has_legacy_identity_exemption(video_id: int) -> bool:
    """Return whether a valid exemption exists; malformed rows fail loudly."""
    rows = AuditLedger.objects.filter(
        object_type=_OBJECT_TYPE,
        object_pk=str(video_id),
        action=LEGACY_IDENTITY_EXEMPTION_ACTION,
    ).values_list("data", flat=True)
    found = False
    for data in rows:
        LegacyIdentityExemptionPayload.model_validate(data)
        found = True
    return found


def legacy_identity_candidates(cutoff: datetime) -> list[LegacyIdentityCandidate]:
    """Validated videos created before ``cutoff`` without verified identity."""
    _require_past_cutoff(cutoff)
    exempted = set(
        AuditLedger.objects.filter(
            object_type=_OBJECT_TYPE, action=LEGACY_IDENTITY_EXEMPTION_ACTION
        ).values_list("object_pk", flat=True)
    )
    rows = (
        VideoFile.objects.filter(
            state__anonymization_validated=True,
            state__date_created__lt=cutoff,
            sensitive_meta__isnull=False,
        )
        .exclude(
            sensitive_meta__state__dob_verified=True,
            sensitive_meta__state__names_verified=True,
        )
        .order_by("pk")
        .values_list("pk", "state__date_created")
    )
    candidates: list[LegacyIdentityCandidate] = []
    for pk, created in rows:
        if str(pk) in exempted:
            continue
        if not isinstance(created, datetime):
            raise TypeError(f"video {pk} has no state creation time")
        candidates.append(
            LegacyIdentityCandidate(video_id=int(pk), state_created_at=created)
        )
    return candidates


def record_legacy_identity_exemptions(
    candidates: Iterable[LegacyIdentityCandidate],
    *,
    cutoff: datetime,
    actor: str,
    reason: str,
) -> int:
    """Append one exemption per candidate; repeated runs write nothing new."""
    _require_past_cutoff(cutoff)
    written = 0
    for candidate in candidates:
        if candidate.state_created_at >= cutoff:
            raise ValueError(f"video {candidate.video_id} was created after the cutoff")
        payload = LegacyIdentityExemptionPayload(
            cutoff=cutoff,
            state_created_at=candidate.state_created_at,
            actor=actor,
            reason=reason,
        )
        with transaction.atomic():
            # Serialize concurrent runs per video before the existence check.
            if (
                not VideoFile.objects.select_for_update()
                .filter(pk=candidate.video_id)
                .exists()
            ):
                raise ValueError(f"video {candidate.video_id} no longer exists")
            if has_legacy_identity_exemption(candidate.video_id):
                continue
            AuditLedger.objects.create(
                object_type=_OBJECT_TYPE,
                object_pk=str(candidate.video_id),
                action=LEGACY_IDENTITY_EXEMPTION_ACTION,
                data=payload.model_dump(mode="json"),
            )
            written += 1
    return written


__all__ = [
    "LEGACY_IDENTITY_EXEMPTION_ACTION",
    "LegacyIdentityCandidate",
    "LegacyIdentityExemptionPayload",
    "has_legacy_identity_exemption",
    "legacy_identity_candidates",
    "record_legacy_identity_exemptions",
]
