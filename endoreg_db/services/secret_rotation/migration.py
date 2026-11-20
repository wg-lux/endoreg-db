"""Whole-run identity migration with one generation snapshot and explicit recovery policy."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.db import transaction

from endoreg_db.config.identity_hashing import current_identity_keyring
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.models.administration.person.examiner.examiner import Examiner
from endoreg_db.services.secret_rotation.identity import (
    ReviewedIdentity,
    ReviewedExaminer,
    identity_rotation_transaction,
    migrate_identity_group,
    migrate_reviewed_examiner,
    salt_fingerprint,
)
from endoreg_db.utils.structured_logging import emit_structured_event

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IdentityMigrationResult:
    patient_groups: int
    metadata_rows: int
    blocked_groups: int
    applied: bool
    rolled_back: bool
    pending_examiners: int
    examiner_failures: int

    @property
    def incomplete(self) -> bool:
        return bool(
            self.blocked_groups or self.pending_examiners or self.examiner_failures
        )


@identity_rotation_transaction
def rotate_identity_salt(
    *,
    apply: bool = False,
    allow_partial: bool = False,
    compromised: bool = False,
    expected_active_fingerprint: str | None = None,
    reviewed: dict[int, ReviewedIdentity] | None = None,
    examiners: tuple[ReviewedExaminer, ...] = (),
) -> IdentityMigrationResult:
    """Preview or atomically migrate eligible groups; never provision or retire keys.

    Compromised recovery requires independently reviewed originals for every row,
    since matching a compromised hash alone does not establish patient identity.
    """
    if allow_partial and (not apply or compromised):
        raise ValueError(
            "Partial migration requires apply and is forbidden in compromised mode"
        )
    ring = current_identity_keyring()
    if ring is None:
        raise ValueError("An identity salt manifest must be explicitly configured")
    if compromised and expected_active_fingerprint is None:
        raise ValueError("Compromised recovery requires an expected active fingerprint")
    if (
        expected_active_fingerprint is not None
        and expected_active_fingerprint != salt_fingerprint(ring.active)
    ):
        raise ValueError("Active identity salt does not match the expected generation")
    reviewed = reviewed or {}
    if any(pk != item.sensitive_meta_id for pk, item in reviewed.items()):
        raise ValueError("Reviewed identity identifiers must match their mapping keys")
    if len({item.examiner_id for item in examiners}) != len(examiners):
        raise ValueError("Reviewed examiner records must be unique")
    active = salt_fingerprint(ring.active)
    migrated = blocked = rows = 0
    groups: set[tuple[int | None, str]] = set()
    metadata = SensitiveMeta.objects.all()
    examiner_inventory = Examiner.objects.all()
    if ring.center_id is not None:
        metadata = metadata.filter(center__center_key=ring.center_id)
        examiner_inventory = examiner_inventory.filter(
            center__center_key=ring.center_id
        )
    for row in (
        metadata.filter(external_id__isnull=True)
        .exclude(patient_hash__isnull=True)
        .exclude(patient_hash="")
        .exclude(identity_salt_fingerprint=active)
        .select_related("center")
        .iterator()
    ):
        group_key = (row.center_id, str(row.patient_hash))
        if group_key in groups:
            continue
        groups.add(group_key)
        try:
            if compromised:
                group_rows = SensitiveMeta.objects.filter(
                    pseudo_patient_id=row.pseudo_patient_id
                )
                if row.pseudo_patient_id is None or any(
                    int(item.pk) not in reviewed for item in group_rows
                ):
                    raise ValueError(
                        "Compromised recovery requires reviewed originals for every group member"
                    )
            count = migrate_identity_group(row, reviewed=reviewed, apply=apply)
            if count == 0:
                blocked += 1
            else:
                rows += count
                migrated += 1
        except ValueError as exc:
            blocked += 1
            emit_structured_event(
                logger,
                "identity_rotation_blocked",
                level=logging.ERROR,
                sensitive_meta_id=int(row.pk),
                detail=str(exc),
            )
    examiner_failures = 0
    previewed_examiners: set[int] = set()
    for examiner in examiners:
        try:
            migrate_reviewed_examiner(examiner, apply=apply)
            previewed_examiners.add(examiner.examiner_id)
        except (ValueError, Examiner.DoesNotExist) as exc:
            examiner_failures += 1
            emit_structured_event(
                logger,
                "examiner_rotation_blocked",
                level=logging.ERROR,
                examiner_id=examiner.examiner_id,
                detail=str(exc),
            )
    pending_examiners = (
        examiner_inventory.exclude(identity_salt_fingerprint=active)
        .exclude(pk__in=previewed_examiners)
        .count()
    )
    incomplete = bool(blocked or examiner_failures or pending_examiners)
    rollback = apply and not allow_partial and incomplete
    if rollback:
        transaction.set_rollback(True)
    return IdentityMigrationResult(
        patient_groups=migrated,
        metadata_rows=rows,
        blocked_groups=blocked,
        applied=apply and not rollback,
        rolled_back=rollback,
        pending_examiners=pending_examiners,
        examiner_failures=examiner_failures,
    )
