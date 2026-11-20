"""Receiver-side hub record identity hashes and conflict decisions.

The hashes contain no source demographic hash or salt: hub-scoped pseudonyms
(contract 6.0) are bound to the authenticated source node and center namespace.
Callers own row locks, row creation and the surrounding transaction; these
functions only derive values and reject conflicting identities.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from endoreg_db.utils.hashs import get_bytes_hash

if TYPE_CHECKING:
    from endoreg_db.models.administration.person.patient.patient import Patient
    from endoreg_db.models.medical.patient.patient_examination import (
        PatientExamination,
    )
    from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta


@dataclass(frozen=True)
class HubRecordIdentityHashes:
    patient_hash: str
    examination_hash: str


def derive_hub_record_identity_hashes(
    *,
    source_node_pk: int,
    center_pk: int,
    patient_uuid: UUID,
    examination_uuid: UUID,
) -> HubRecordIdentityHashes:
    """Bind received pseudonyms to the authenticated source node and center."""
    namespace = f"hub-record-v5:{source_node_pk}:{center_pk}"
    return HubRecordIdentityHashes(
        patient_hash=get_bytes_hash(
            f"{namespace}:patient:{patient_uuid}".encode("utf-8")
        ),
        examination_hash=get_bytes_hash(
            f"{namespace}:examination:{examination_uuid}".encode("utf-8")
        ),
    )


def assert_patient_owned_by_source(
    *, patient: Patient | None, center_pk: int, patient_hash: str
) -> None:
    """Reject a known patient UUID that another source namespace owns."""
    if patient is not None and (
        patient.center_id != center_pk or patient.patient_hash != patient_hash
    ):
        raise ValueError("Hub patient UUID belongs to a different source owner.")


def assert_existing_meta_matches_identity(
    *,
    existing: SensitiveMeta,
    center_pk: int,
    patient: Patient | None,
    hashes: HubRecordIdentityHashes,
    resolve_previous_patient: Callable[[str], Patient | None],
) -> None:
    """Reject reassignment of locked sensitive metadata to another identity.

    ``resolve_previous_patient`` is only called when the stored patient hash
    differs from the received one.
    """
    if existing.center_id != center_pk:
        raise ValueError("Hub sensitive metadata belongs to a different center.")
    if existing.pseudo_patient_id is not None and (
        patient is None or existing.pseudo_patient_id != patient.pk
    ):
        raise ValueError("Hub sensitive metadata is linked to a different patient.")
    if existing.patient_hash and existing.patient_hash != hashes.patient_hash:
        previous_patient = resolve_previous_patient(existing.patient_hash)
        if patient is None or previous_patient != patient:
            raise ValueError("Hub sensitive metadata has a different patient identity.")
    if (
        existing.examination_hash
        and existing.examination_hash != hashes.examination_hash
    ):
        raise ValueError("Hub sensitive metadata has a different examination identity.")


def assert_examination_matches_identity(
    *,
    patient_examination: PatientExamination | None,
    existing: SensitiveMeta | None,
    patient: Patient | None,
    examination_hash: str,
) -> None:
    """Reject an examination UUID linked to another patient or source namespace."""
    if patient_examination is not None and (
        patient is None
        or patient_examination.patient_id != patient.pk
        or patient_examination.hash != examination_hash
    ):
        raise ValueError(
            "Hub examination_hash is already linked to a different patient_hash."
        )
    if (
        existing is not None
        and existing.pseudo_examination_id is not None
        and (
            patient_examination is None
            or existing.pseudo_examination_id != patient_examination.pk
        )
    ):
        raise ValueError("Hub sensitive metadata is linked to a different examination.")
