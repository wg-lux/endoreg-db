from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Generator
from contextlib import contextmanager
from datetime import date
from typing import Literal, Protocol, cast

from django.db import transaction

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.models.medical.examination.examination import Examination
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.services.cases.resolution_state import (
    get_case_resolution_meta,
    persist_auto_case_resolution_state,
)


@dataclass(slots=True)
class AutoCaseResolutionResult:
    status: Literal["linked", "ambiguous", "unresolved"]
    patient_examination: PatientExamination | None = None
    created: bool = False
    reason: str | None = None


class _SavableModel(Protocol):
    def save(self, *args: object, **kwargs: object) -> None: ...


def _model_value(instance: object, field_name: str) -> object:
    return getattr(instance, field_name)


def _model_optional_int(instance: object, field_name: str) -> int | None:
    value = _model_value(instance, field_name)
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float | str):
        return int(value)
    raise TypeError(f"{field_name} must be numeric.")


def _model_required_int(instance: object, field_name: str) -> int:
    value = _model_optional_int(instance, field_name)
    if value is None:
        raise ValueError(f"{field_name} is required.")
    return value


def _model_optional_date(instance: object, field_name: str) -> date | None:
    value = _model_value(instance, field_name)
    if value is None:
        return None
    if isinstance(value, date):
        return value
    raise TypeError(f"{field_name} must be a date.")


def _model_save(instance: object, *, update_fields: list[str]) -> None:
    cast(_SavableModel, instance).save(update_fields=update_fields)


def _model_relation(instance: object, field_name: str) -> object:
    return _model_value(instance, field_name)


def _resolved_examination(
    media_obj: RawPdfFile | VideoFile,
) -> Examination | None:
    sensitive_meta = _model_relation(media_obj, "sensitive_meta")
    if sensitive_meta is None:
        return None

    pseudo_examination = _model_relation(sensitive_meta, "pseudo_examination")
    if pseudo_examination is not None:
        return cast(Examination, _model_relation(pseudo_examination, "examination"))
    return None


def _validate_inferred_identity(
    media_obj: RawPdfFile | VideoFile,
    patient_examination: PatientExamination | None = None,
) -> Center:
    sensitive_meta = _model_relation(media_obj, "sensitive_meta")
    if sensitive_meta is None:
        raise ValueError("Automatic case resolution requires sensitive metadata.")
    center = _model_relation(sensitive_meta, "center")
    if not isinstance(center, Center):
        raise ValueError("Automatic case resolution requires a center.")
    if media_obj.center_id is not None and media_obj.center_id != center.pk:
        raise ValueError("Media and sensitive metadata centers conflict.")
    patient = Patient.get_pseudo_patient_by_hash(
        str(_model_value(sensitive_meta, "patient_hash") or ""), center=center
    )
    if patient is None:
        raise ValueError("Automatic case resolution requires a known patient.")
    for linked_patient_id in (
        _model_optional_int(sensitive_meta, "pseudo_patient_id"),
        _model_optional_int(media_obj, "patient_id"),
    ):
        if linked_patient_id is not None and linked_patient_id != patient.pk:
            raise ValueError("Persisted patient links conflict with the patient hash.")
    pseudo_examination = _model_relation(sensitive_meta, "pseudo_examination")
    if pseudo_examination is not None:
        if _model_optional_int(
            pseudo_examination, "patient_id"
        ) != patient.pk or _model_value(pseudo_examination, "hash") != _model_value(
            sensitive_meta, "examination_hash"
        ):
            raise ValueError("Persisted examination conflicts with sensitive metadata.")
    if patient_examination is not None and (
        patient_examination.patient_id != patient.pk
        or patient_examination.hash != _model_value(sensitive_meta, "examination_hash")
    ):
        raise ValueError("Linked examination conflicts with sensitive metadata.")
    return center


@contextmanager
def correct_inferred_case_identity(
    *, media_type: Literal["video", "pdf"], media_obj: RawPdfFile | VideoFile
) -> Generator[None]:
    """Rebind a consistent inferred case only around an authorized metadata edit.

    The caller must hold ``identity_rotation_transaction`` before entering this
    context so the identity fence precedes media and metadata row locks.
    """
    with transaction.atomic():
        type(media_obj).objects.select_for_update().get(pk=media_obj.pk)
        media_obj.refresh_from_db()
        if media_obj.sensitive_meta_id is not None:
            media_obj.sensitive_meta = SensitiveMeta.objects.select_for_update().get(
                pk=media_obj.sensitive_meta_id
            )
        resolution = get_case_resolution_meta(media_obj)
        inferred = (
            media_obj.examination_id is not None
            and not resolution.get("is_explicitly_resolved")
            and not resolution.get("deferred")
        )
        if inferred:
            _validate_inferred_identity(media_obj, media_obj.examination)
        sensitive_meta = media_obj.sensitive_meta
        previous_hashes = (
            (sensitive_meta.patient_hash, sensitive_meta.examination_hash)
            if sensitive_meta is not None
            else None
        )
        yield
        sensitive_meta = media_obj.sensitive_meta
        current_hashes = (
            (sensitive_meta.patient_hash, sensitive_meta.examination_hash)
            if sensitive_meta is not None
            else None
        )
        if inferred and current_hashes != previous_hashes:
            # The old relationship was checked before the edit. Only this
            # authorized transition may replace it with the corrected identity.
            media_obj.examination = None
            media_obj.patient = None
            result = auto_resolve_media_case(media_type=media_type, media_obj=media_obj)
            if result.status != "linked":
                raise ValueError("Corrected metadata must resolve to a clinical case.")


def link_media_to_patient_examination(
    *,
    media_type: Literal["video", "pdf"],
    media_obj: RawPdfFile | VideoFile,
    patient_examination: PatientExamination,
) -> None:
    update_fields: list[str] = []

    if _model_optional_int(media_obj, "examination_id") != _model_required_int(
        patient_examination,
        "pk",
    ):
        setattr(media_obj, "examination", patient_examination)
        update_fields.append("examination")
    if _model_optional_int(media_obj, "patient_id") != _model_optional_int(
        patient_examination,
        "patient_id",
    ):
        setattr(media_obj, "patient", _model_relation(patient_examination, "patient"))
        update_fields.append("patient")

    if update_fields:
        _model_save(media_obj, update_fields=update_fields)


def _hydrate_inferred_patient_examination(
    *,
    patient_examination: PatientExamination,
    media_obj: RawPdfFile | VideoFile,
) -> None:
    sensitive_meta = _model_relation(media_obj, "sensitive_meta")
    if sensitive_meta is None:
        return

    update_fields: list[str] = []
    inferred_examination = _resolved_examination(media_obj)

    if _model_optional_int(
        patient_examination, "patient_id"
    ) is None and _model_optional_int(
        sensitive_meta,
        "pseudo_patient_id",
    ):
        setattr(
            patient_examination,
            "patient",
            _model_relation(sensitive_meta, "pseudo_patient"),
        )
        update_fields.append("patient")
    if (
        _model_optional_int(patient_examination, "examination_id") is None
        and inferred_examination is not None
    ):
        setattr(patient_examination, "examination", inferred_examination)
        update_fields.append("examination")
    examination_date = _model_optional_date(sensitive_meta, "examination_date")
    if _model_value(patient_examination, "date_start") is None and examination_date:
        setattr(patient_examination, "date_start", examination_date)
        update_fields.append("date_start")

    if update_fields:
        _model_save(patient_examination, update_fields=update_fields)


@transaction.atomic
def auto_resolve_media_case(
    *,
    media_type: Literal["video", "pdf"],
    media_obj: RawPdfFile | VideoFile,
) -> AutoCaseResolutionResult:
    media_examination_id = _model_optional_int(media_obj, "examination_id")
    if media_examination_id is not None:
        patient_examination = (
            PatientExamination.objects.select_related("patient", "examination")
            .filter(pk=media_examination_id)
            .first()
        )
        case_resolution_meta = get_case_resolution_meta(media_obj)
        if (
            patient_examination is not None
            and not case_resolution_meta.get("is_explicitly_resolved")
            and not case_resolution_meta.get("deferred")
        ):
            _validate_inferred_identity(media_obj, patient_examination)
            persist_auto_case_resolution_state(
                media_obj=media_obj,
                patient_examination_id=_model_required_int(
                    patient_examination,
                    "pk",
                ),
                patient_id=_model_required_int(patient_examination, "patient_id"),
                created=False,
            )
        return AutoCaseResolutionResult(
            status="linked",
            patient_examination=patient_examination,
            created=False,
            reason="already_linked",
        )

    sensitive_meta = _model_relation(media_obj, "sensitive_meta")
    if sensitive_meta is None:
        return AutoCaseResolutionResult(
            status="unresolved", reason="missing_sensitive_meta"
        )

    patient_hash = str(_model_value(sensitive_meta, "patient_hash") or "")
    examination_hash = str(_model_value(sensitive_meta, "examination_hash") or "")
    if not patient_hash or not examination_hash:
        return AutoCaseResolutionResult(status="unresolved", reason="missing_hashes")

    center = _validate_inferred_identity(media_obj)
    inferred_examination = _resolved_examination(media_obj)
    patient_examination, created = (
        PatientExamination.get_or_create_pseudo_patient_examination_by_hash(
            patient_hash=patient_hash,
            examination_hash=examination_hash,
            center=center,
            examination_name=(
                str(_model_value(inferred_examination, "name"))
                if inferred_examination is not None
                else None
            ),
        )
    )
    _hydrate_inferred_patient_examination(
        patient_examination=patient_examination,
        media_obj=media_obj,
    )
    link_media_to_patient_examination(
        media_type=media_type,
        media_obj=media_obj,
        patient_examination=patient_examination,
    )
    persist_auto_case_resolution_state(
        media_obj=media_obj,
        patient_examination_id=_model_required_int(patient_examination, "pk"),
        patient_id=_model_required_int(patient_examination, "patient_id"),
        created=created,
    )
    return AutoCaseResolutionResult(
        status="linked",
        patient_examination=patient_examination,
        created=created,
        reason="created_from_sensitive_meta" if created else "matched_by_hash",
    )


__all__ = [
    "AutoCaseResolutionResult",
    "auto_resolve_media_case",
    "correct_inferred_case_identity",
    "link_media_to_patient_examination",
]
