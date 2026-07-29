"""Lazy host persistence adapter consumed by ``lx_dtypes.django.api``."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal, TypeAlias, cast

import django
from django.apps import apps
from django.db import models
from django.db.models import QuerySet
from lx_dtypes.models.contracts.json_types import JsonValue
from pydantic import BaseModel
from rest_framework.exceptions import AuthenticationFailed

from endoreg_db.authz.auth import KeycloakJWTAuthentication
from endoreg_db.authz.policy import satisfies
from endoreg_db.services.center_access import resolve_allowed_center_ids

if TYPE_CHECKING:
    from endoreg_db.models.administration.center.center import Center
    from endoreg_db.models.administration.person.patient.patient import Patient
    from endoreg_db.models.medical.examination.examination import Examination
    from endoreg_db.models.medical.finding.finding import Finding
    from endoreg_db.models.medical.finding.finding_classification import (
        FindingClassification,
        FindingClassificationChoice,
    )
    from endoreg_db.models.medical.patient.patient_examination import (
        PatientExamination,
    )
    from endoreg_db.models.medical.patient.patient_finding import PatientFinding
    from endoreg_db.models.medical.patient.patient_finding_classification import (
        PatientFindingClassification,
    )
    from endoreg_db.models.other.gender import Gender

DjangoModel: TypeAlias = type[models.Model]
HostModelMap: TypeAlias = Mapping[str, DjangoModel]
HostModelName: TypeAlias = Literal[
    "Center",
    "Examination",
    "Finding",
    "FindingClassification",
    "FindingClassificationChoice",
    "Gender",
    "Patient",
    "PatientDisease",
    "PatientEvent",
    "PatientExamination",
    "PatientFinding",
    "PatientFindingClassification",
    "PatientLabSample",
    "PatientLabValue",
    "PatientMedication",
    "PatientMedicationSchedule",
]

_HOST_MODEL_NAMES: tuple[HostModelName, ...] = (
    "Center",
    "Examination",
    "Finding",
    "FindingClassification",
    "FindingClassificationChoice",
    "Gender",
    "Patient",
    "PatientDisease",
    "PatientEvent",
    "PatientExamination",
    "PatientFinding",
    "PatientFindingClassification",
    "PatientLabSample",
    "PatientLabValue",
    "PatientMedication",
    "PatientMedicationSchedule",
)


def _ensure_django_apps_ready() -> None:
    if not apps.ready:
        django.setup()


def _resolve_model(name: HostModelName) -> DjangoModel:
    _ensure_django_apps_ready()
    return apps.get_model("endoreg_db", name)


def _patient_examination_model() -> type[PatientExamination]:
    return cast("type[PatientExamination]", _resolve_model("PatientExamination"))


def _finding_model() -> type[Finding]:
    return cast("type[Finding]", _resolve_model("Finding"))


def _finding_classification_model() -> type[FindingClassification]:
    return cast(
        "type[FindingClassification]",
        _resolve_model("FindingClassification"),
    )


def _finding_classification_choice_model() -> type[FindingClassificationChoice]:
    return cast(
        "type[FindingClassificationChoice]",
        _resolve_model("FindingClassificationChoice"),
    )


def _patient_finding_model() -> type[PatientFinding]:
    return cast("type[PatientFinding]", _resolve_model("PatientFinding"))


def _patient_finding_classification_model() -> type[PatientFindingClassification]:
    return cast(
        "type[PatientFindingClassification]",
        _resolve_model("PatientFindingClassification"),
    )


def __getattr__(name: str) -> DjangoModel:
    if name in _HOST_MODEL_NAMES:
        return _resolve_model(name)
    raise AttributeError(name)


def orm_models() -> HostModelMap:
    """Return the explicit model map exposed to lx-dtypes routes."""
    return {name: _resolve_model(name) for name in _HOST_MODEL_NAMES}


def get_patient_examination(
    patient_examination_id: int,
) -> PatientExamination | None:
    model = _patient_examination_model()
    return (
        model.objects.select_related("patient", "examination")
        .filter(id=patient_examination_id)
        .first()
    )


def get_finding(finding_id: int) -> Finding | None:
    model = _finding_model()
    return model.objects.filter(id=finding_id).first()


def get_finding_classification(
    classification_id: int,
) -> FindingClassification | None:
    model = _finding_classification_model()
    return model.objects.filter(id=classification_id).first()


def get_finding_classification_choice(
    choice_id: int,
) -> FindingClassificationChoice | None:
    model = _finding_classification_choice_model()
    return model.objects.filter(id=choice_id).first()


def active_patient_findings_queryset() -> QuerySet[PatientFinding]:
    model = _patient_finding_model()
    return model.objects.filter(is_active=True).select_related(
        "patient_examination__patient",
        "finding",
    )


def get_active_patient_finding(patient_finding_id: int) -> PatientFinding | None:
    return active_patient_findings_queryset().filter(id=patient_finding_id).first()


def create_patient_finding(
    *,
    patient_examination: PatientExamination,
    finding: Finding,
) -> PatientFinding:
    model = _patient_finding_model()
    return model.objects.create(
        patient_examination=patient_examination,
        finding=finding,
    )


def create_patient_finding_classification(
    *,
    patient_finding: PatientFinding,
    classification: FindingClassification,
    classification_choice: FindingClassificationChoice,
) -> PatientFindingClassification:
    model = _patient_finding_classification_model()
    return model.objects.create(
        finding=patient_finding,
        classification=classification,
        classification_choice=classification_choice,
        is_active=True,
    )


def authenticate_request_user(request: Any) -> Any | None:
    """Resolve a session or verified Bearer principal for lx-dtypes routes."""
    user = getattr(request, "user", None)
    if getattr(user, "is_authenticated", False):
        return user

    try:
        authentication_result = KeycloakJWTAuthentication().authenticate(request)
    except AuthenticationFailed:
        return None
    if authentication_result is None:
        return None

    authenticated_user = authentication_result[0]
    request.user = authenticated_user
    return authenticated_user


def patient_finding_access_allowed(request: Any, patient_finding: object) -> bool:
    """Authorize one patient finding without revealing foreign-center records."""
    patient_examination = getattr(patient_finding, "patient_examination", None)
    return patient_examination_access_allowed(request, patient_examination)


def patient_examination_access_allowed(
    request: Any, patient_examination: object
) -> bool:
    """Authorize one examination from the authenticated user's center scope."""
    user = getattr(request, "user", None)
    if not getattr(user, "is_authenticated", False):
        return False

    allowed_center_ids = resolve_allowed_center_ids(user)
    if allowed_center_ids is None:
        return True
    if not allowed_center_ids:
        return False

    patient = getattr(patient_examination, "patient", None)
    object_center_id = getattr(patient, "center_id", None)
    return isinstance(object_center_id, int) and object_center_id in allowed_center_ids


def patient_findings_queryset_for_request(
    request: Any,
) -> QuerySet[PatientFinding]:
    """Return active findings restricted to the authenticated center scope."""
    queryset = active_patient_findings_queryset()
    user = getattr(request, "user", None)
    if not getattr(user, "is_authenticated", False):
        return queryset.none()

    allowed_center_ids = resolve_allowed_center_ids(user)
    if allowed_center_ids is None:
        return queryset
    if not allowed_center_ids:
        return queryset.none()
    return queryset.filter(
        patient_examination__patient__center_id__in=allowed_center_ids
    )


def terminology_write_access_allowed(actor: object) -> bool:
    """Restrict terminology registry mutations to explicit write-capable actors."""
    if bool(getattr(actor, "is_superuser", False) or getattr(actor, "is_staff", False)):
        return True
    groups = getattr(actor, "groups", None)
    if groups is None:
        return False
    role_names = set(groups.values_list("name", flat=True))
    return "terminology:write" in role_names or satisfies(role_names, "data:write")


def persist_patient_examination_dtypes_record(
    patient_examination: PatientExamination,
    payload: BaseModel | Mapping[str, JsonValue],
) -> dict[str, Any]:
    from endoreg_db.services.dtypes_records import (
        persist_patient_examination_dtypes_record as persist_record,
    )

    return persist_record(patient_examination, payload)


__all__ = [
    "Center",
    "DjangoModel",
    "Examination",
    "Finding",
    "FindingClassification",
    "FindingClassificationChoice",
    "Gender",
    "HostModelMap",
    "Patient",
    "PatientExamination",
    "PatientFinding",
    "PatientFindingClassification",
    "active_patient_findings_queryset",
    "authenticate_request_user",
    "create_patient_finding",
    "create_patient_finding_classification",
    "get_active_patient_finding",
    "get_finding",
    "get_finding_classification",
    "get_finding_classification_choice",
    "get_patient_examination",
    "orm_models",
    "patient_examination_access_allowed",
    "patient_finding_access_allowed",
    "patient_findings_queryset_for_request",
    "terminology_write_access_allowed",
    "persist_patient_examination_dtypes_record",
]
