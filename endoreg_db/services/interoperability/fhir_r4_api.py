"""Read-only FHIR R4 REST API over the pseudonymized examination export.

Resources are never assembled independently: every clinical resource is taken
from the validated examination Bundle built by ``fhir_r4``, so a read through
this API is byte-identical to the same resource in an exported Bundle. Callers
pass an already center-scoped examination queryset; this module adds no
authorization of its own and widens nothing.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Literal, cast

from django.db.models import QuerySet
from lx_dtypes.terminology.terminology_service import TerminologyError

from endoreg_db.exceptions import FhirExportError
from endoreg_db.models.interoperability.dicom import DicomStudy
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.medical.patient.patient_finding import PatientFinding
from endoreg_db.models.report.patient_examination_report import (
    PatientExaminationReport,
)
from endoreg_db.schemas.fhir_r4 import (
    FhirBundleLink,
    FhirBundleSearch,
    FhirCapabilityInteraction,
    FhirCapabilityOperation,
    FhirCapabilityResource,
    FhirCapabilityRest,
    FhirCapabilitySearchParam,
    FhirCapabilityStatement,
    FhirOperationOutcome,
    FhirOperationOutcomeIssue,
    FhirReadableResource,
    FhirSearchBundle,
    FhirSearchBundleEntry,
    FhirTerminologyResource,
)
from endoreg_db.services.interoperability.fhir_r4 import (
    build_patient_examination_fhir_bundle,
    fhir_resource_id,
)
from endoreg_db.utils.structured_logging import emit_structured_event

logger = logging.getLogger("endoreg_db.interoperability.fhir_api")

FHIR_PAGE_DEFAULT = 50
FHIR_PAGE_MAX = 200
# $everything builds one Bundle per examination; bound the work per request.
FHIR_EVERYTHING_MAX_EXAMINATIONS = 200

IssueCode = Literal[
    "invalid", "not-found", "not-supported", "forbidden", "processing", "exception"
]


class ClinicalResourceType(StrEnum):
    PATIENT = "Patient"
    PROCEDURE = "Procedure"
    OBSERVATION = "Observation"
    DIAGNOSTIC_REPORT = "DiagnosticReport"
    IMAGING_STUDY = "ImagingStudy"


class TerminologyResourceType(StrEnum):
    CODE_SYSTEM = "CodeSystem"
    VALUE_SET = "ValueSet"


_ID_PREFIX: Mapping[ClinicalResourceType, str] = {
    ClinicalResourceType.PATIENT: "patient",
    ClinicalResourceType.PROCEDURE: "procedure",
    ClinicalResourceType.OBSERVATION: "observation",
    ClinicalResourceType.DIAGNOSTIC_REPORT: "diagnosticreport",
    ClinicalResourceType.IMAGING_STUDY: "imagingstudy",
}

# Search parameters accepted per type. Unknown parameters are rejected rather
# than ignored so a typo can never silently return a broader result set.
_PAGING_PARAMS = frozenset({"_count", "_offset", "_format"})
_CLINICAL_SEARCH_PARAMS: Mapping[ClinicalResourceType, frozenset[str]] = {
    ClinicalResourceType.PATIENT: frozenset({"_id"}),
    ClinicalResourceType.PROCEDURE: frozenset({"_id", "patient", "subject"}),
    ClinicalResourceType.OBSERVATION: frozenset({"_id", "patient", "subject"}),
    ClinicalResourceType.DIAGNOSTIC_REPORT: frozenset({"_id", "patient", "subject"}),
    ClinicalResourceType.IMAGING_STUDY: frozenset({"_id", "patient", "subject"}),
}
_TERMINOLOGY_SEARCH_PARAMS = frozenset({"_id", "url"})


class FhirApiError(Exception):
    """A request the API answers with an OperationOutcome and HTTP status."""

    status: int
    code: IssueCode
    diagnostics: str

    def __init__(self, status: int, code: IssueCode, diagnostics: str) -> None:
        super().__init__(diagnostics)
        self.status = status
        self.code = code
        self.diagnostics = diagnostics

    def outcome(self) -> FhirOperationOutcome:
        return FhirOperationOutcome(
            issue=[
                FhirOperationOutcomeIssue(
                    severity="error", code=self.code, diagnostics=self.diagnostics
                )
            ]
        )


@dataclass(frozen=True)
class _ResourceKey:
    """One resource in the scope: the examination whose Bundle contains it."""

    examination_pk: int
    resource_id: str


@dataclass(frozen=True)
class SearchPage:
    count: int
    offset: int


def parse_clinical_type(value: str) -> ClinicalResourceType:
    try:
        return ClinicalResourceType(value)
    except ValueError:
        raise FhirApiError(
            404, "not-supported", f"Resource type {value!r} is not supported."
        ) from None


def parse_terminology_type(value: str) -> TerminologyResourceType | None:
    try:
        return TerminologyResourceType(value)
    except ValueError:
        return None


def parse_page(params: Mapping[str, Sequence[str]]) -> SearchPage:
    count = _single_int(params, "_count", FHIR_PAGE_DEFAULT)
    offset = _single_int(params, "_offset", 0)
    if not 1 <= count <= FHIR_PAGE_MAX:
        raise FhirApiError(
            400, "invalid", f"_count must be between 1 and {FHIR_PAGE_MAX}."
        )
    if offset < 0:
        raise FhirApiError(400, "invalid", "_offset must not be negative.")
    _require_json_format(params)
    return SearchPage(count=count, offset=offset)


def _require_json_format(params: Mapping[str, Sequence[str]]) -> None:
    for value in params.get("_format", []):
        if value not in {"json", "application/fhir+json", "application/json"}:
            raise FhirApiError(
                400, "not-supported", "Only the FHIR JSON format is supported."
            )


def _single(params: Mapping[str, Sequence[str]], name: str) -> str | None:
    values = params.get(name, [])
    if len(values) > 1:
        raise FhirApiError(400, "invalid", f"Parameter {name} may be given once.")
    if not values:
        return None
    value = values[0].strip()
    if not value:
        raise FhirApiError(400, "invalid", f"Parameter {name} must not be empty.")
    return value


def _single_int(params: Mapping[str, Sequence[str]], name: str, default: int) -> int:
    value = _single(params, name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        raise FhirApiError(400, "invalid", f"{name} must be an integer.") from None


def _reject_unknown(
    params: Mapping[str, Sequence[str]], allowed: frozenset[str]
) -> None:
    unknown = sorted(set(params) - allowed - _PAGING_PARAMS)
    if unknown:
        raise FhirApiError(
            400,
            "not-supported",
            f"Unsupported search parameter(s): {', '.join(unknown)}.",
        )


def _patient_filter(params: Mapping[str, Sequence[str]]) -> str | None:
    patient = _single(params, "patient")
    subject = _single(params, "subject")
    if patient is not None and subject is not None and patient != subject:
        raise FhirApiError(400, "invalid", "patient and subject must agree.")
    value = patient or subject
    if value is None:
        return None
    resource_id = value.removeprefix("Patient/")
    if "/" in resource_id:
        raise FhirApiError(400, "invalid", "patient must reference a Patient.")
    return resource_id


# --- Resource index ---------------------------------------------------------
#
# Keys mirror the selections ``fhir_r4`` makes when it builds a Bundle. If the
# two ever disagree, ``_resources_for_keys`` fails loudly instead of returning
# a partial result.


def _ordered_pks(scope: QuerySet[PatientExamination]) -> QuerySet[PatientExamination]:
    return scope.order_by("pk")


def _clinical_keys(
    scope: QuerySet[PatientExamination], resource_type: ClinicalResourceType
) -> list[_ResourceKey]:
    examinations = _ordered_pks(scope)
    match resource_type:
        case ClinicalResourceType.PATIENT:
            seen: dict[str, _ResourceKey] = {}
            for exam_pk, patient_pk in examinations.values_list("pk", "patient_id"):
                resource_id = fhir_resource_id("patient", patient_pk)
                seen.setdefault(resource_id, _ResourceKey(exam_pk, resource_id))
            return list(seen.values())
        case ClinicalResourceType.PROCEDURE:
            return [
                _ResourceKey(pk, fhir_resource_id("procedure", pk))
                for pk in examinations.values_list("pk", flat=True)
            ]
        case ClinicalResourceType.OBSERVATION:
            rows = (
                PatientFinding.objects.filter(
                    patient_examination__in=examinations, is_active=True
                )
                .order_by("patient_examination_id", "pk")
                .values_list("patient_examination_id", "pk")
            )
            return [
                _ResourceKey(
                    exam_pk, fhir_resource_id("observation", f"{exam_pk}:{pk}")
                )
                for exam_pk, pk in rows
            ]
        case ClinicalResourceType.DIAGNOSTIC_REPORT:
            rows = (
                PatientExaminationReport.objects.filter(
                    patient_examination__in=examinations, is_active=True
                )
                .order_by("patient_examination_id", "pk")
                .values_list("patient_examination_id", "pk")
            )
            return [
                _ResourceKey(
                    exam_pk, fhir_resource_id("diagnosticreport", f"{exam_pk}:{pk}")
                )
                for exam_pk, pk in rows
            ]
        case ClinicalResourceType.IMAGING_STUDY:
            rows = (
                DicomStudy.objects.filter(
                    patient_examination__in=examinations,
                    export_job__status="imported",
                )
                .order_by("patient_examination_id", "pk")
                .values_list("patient_examination_id", "study_instance_uid")
            )
            return [
                _ResourceKey(exam_pk, fhir_resource_id("imagingstudy", uid))
                for exam_pk, uid in rows
            ]


def _examinations_of_patient(
    scope: QuerySet[PatientExamination], patient_resource_id: str
) -> QuerySet[PatientExamination]:
    patient_pks = {
        patient_pk
        for patient_pk in scope.values_list("patient_id", flat=True).distinct()
        if fhir_resource_id("patient", patient_pk) == patient_resource_id
    }
    return scope.filter(patient_id__in=patient_pks)


def _bundle_resources(
    examination: PatientExamination,
) -> dict[tuple[str, str], FhirReadableResource]:
    try:
        bundle = build_patient_examination_fhir_bundle(examination)
    except FhirExportError:
        # The exporter already logged a structured, identity-free rejection.
        raise FhirApiError(
            422,
            "processing",
            "The examination data cannot be represented as FHIR.",
        ) from None
    return {
        (entry.resource.resource_type, entry.resource.id): entry.resource
        for entry in bundle.entry
    }


def _resources_for_keys(
    scope: QuerySet[PatientExamination],
    resource_type: ClinicalResourceType,
    keys: Sequence[_ResourceKey],
) -> list[FhirReadableResource]:
    examinations = {
        examination.pk: examination
        for examination in scope.filter(pk__in={key.examination_pk for key in keys})
    }
    bundles: dict[int, dict[tuple[str, str], FhirReadableResource]] = {}
    resources: list[FhirReadableResource] = []
    for key in keys:
        if key.examination_pk not in bundles:
            bundles[key.examination_pk] = _bundle_resources(
                examinations[key.examination_pk]
            )
        resource = bundles[key.examination_pk].get(
            (resource_type.value, key.resource_id)
        )
        if resource is None:
            emit_structured_event(
                logger,
                "fhir.api_index_mismatch",
                level=logging.ERROR,
                resource_type=resource_type.value,
            )
            raise RuntimeError(
                "FHIR API resource index disagrees with the examination exporter"
            )
        resources.append(resource)
    return resources


# --- Interactions -----------------------------------------------------------


def read_clinical_resource(
    scope: QuerySet[PatientExamination],
    resource_type: ClinicalResourceType,
    resource_id: str,
) -> FhirReadableResource:
    if not resource_id.startswith(f"{_ID_PREFIX[resource_type]}-"):
        raise FhirApiError(404, "not-found", f"{resource_type.value} not found.")
    keys = [
        key
        for key in _clinical_keys(scope, resource_type)
        if key.resource_id == resource_id
    ]
    if not keys:
        raise FhirApiError(404, "not-found", f"{resource_type.value} not found.")
    return _resources_for_keys(scope, resource_type, keys[:1])[0]


def search_clinical_resources(
    scope: QuerySet[PatientExamination],
    resource_type: ClinicalResourceType,
    params: Mapping[str, Sequence[str]],
    *,
    self_url: str,
    page_url: Callable[[int], str],
) -> FhirSearchBundle:
    _reject_unknown(params, _CLINICAL_SEARCH_PARAMS[resource_type])
    page = parse_page(params)
    patient_id = (
        _patient_filter(params)
        if resource_type is not ClinicalResourceType.PATIENT
        else None
    )
    if patient_id is not None:
        scope = _examinations_of_patient(scope, patient_id)
    keys = _clinical_keys(scope, resource_type)
    resource_id = _single(params, "_id")
    if resource_id is not None:
        keys = [key for key in keys if key.resource_id == resource_id]
    page_keys = keys[page.offset : page.offset + page.count]
    resources = _resources_for_keys(scope, resource_type, page_keys)
    return _search_bundle(
        resources,
        total=len(keys),
        page=page,
        self_url=self_url,
        page_url=page_url,
    )


def patient_everything(
    scope: QuerySet[PatientExamination],
    patient_resource_id: str,
    *,
    self_url: str,
) -> FhirSearchBundle:
    if not patient_resource_id.startswith("patient-"):
        raise FhirApiError(404, "not-found", "Patient not found.")
    examinations = list(
        _ordered_pks(_examinations_of_patient(scope, patient_resource_id))[
            : FHIR_EVERYTHING_MAX_EXAMINATIONS + 1
        ]
    )
    if not examinations:
        raise FhirApiError(404, "not-found", "Patient not found.")
    if len(examinations) > FHIR_EVERYTHING_MAX_EXAMINATIONS:
        raise FhirApiError(
            400,
            "not-supported",
            "Patient has too many examinations for $everything; search per type.",
        )
    resources: dict[tuple[str, str], FhirReadableResource] = {}
    for examination in examinations:
        for identity, resource in _bundle_resources(examination).items():
            resources.setdefault(identity, resource)
    ordered = list(resources.values())
    return FhirSearchBundle(
        total=len(ordered),
        link=[FhirBundleLink(relation="self", url=self_url)],
        entry=[_search_entry(resource) for resource in ordered] or None,
    )


def _search_entry(resource: FhirReadableResource) -> FhirSearchBundleEntry:
    return FhirSearchBundleEntry(
        fullUrl=f"{resource.resource_type}/{resource.id}",
        resource=resource,
        search=FhirBundleSearch(mode="match"),
    )


def _search_bundle(
    resources: Sequence[FhirReadableResource],
    *,
    total: int,
    page: SearchPage,
    self_url: str,
    page_url: Callable[[int], str],
) -> FhirSearchBundle:
    links = [FhirBundleLink(relation="self", url=self_url)]
    if page.offset + page.count < total:
        links.append(
            FhirBundleLink(relation="next", url=page_url(page.offset + page.count))
        )
    if page.offset > 0:
        links.append(
            FhirBundleLink(
                relation="previous", url=page_url(max(page.offset - page.count, 0))
            )
        )
    return FhirSearchBundle(
        total=total,
        link=links,
        entry=[_search_entry(resource) for resource in resources] or None,
    )


# --- Terminology (owned by lx-dtypes; served for the active bundle) ----------


def _terminology_resources(
    export_active_terminology: Callable[[], Mapping[str, object]],
    resource_type: TerminologyResourceType,
) -> list[FhirTerminologyResource]:
    try:
        bundle = export_active_terminology()
    except TerminologyError as error:
        status = 404 if error.status == 404 else 409
        raise FhirApiError(
            status,
            "not-found" if status == 404 else "processing",
            "The active terminology cannot be exported as FHIR.",
        ) from None
    raw_entries = bundle.get("entry")
    if not isinstance(raw_entries, list):
        raise RuntimeError("terminology FHIR export must contain a Bundle entry list")
    resources: list[FhirTerminologyResource] = []
    for entry in cast(list[object], raw_entries):
        if not isinstance(entry, Mapping):
            raise RuntimeError("terminology FHIR Bundle entries must be objects")
        resource = cast(Mapping[str, object], entry).get("resource")
        if not isinstance(resource, Mapping):
            raise RuntimeError("terminology FHIR Bundle entry must contain a resource")
        typed_resource = cast(Mapping[str, object], resource)
        if typed_resource.get("resourceType") == resource_type.value:
            resources.append(FhirTerminologyResource.model_validate(typed_resource))
    return resources


def read_terminology_resource(
    export_active_terminology: Callable[[], Mapping[str, object]],
    resource_type: TerminologyResourceType,
    resource_id: str,
) -> FhirTerminologyResource:
    for resource in _terminology_resources(export_active_terminology, resource_type):
        if resource.id == resource_id:
            return resource
    raise FhirApiError(404, "not-found", f"{resource_type.value} not found.")


def search_terminology_resources(
    export_active_terminology: Callable[[], Mapping[str, object]],
    resource_type: TerminologyResourceType,
    params: Mapping[str, Sequence[str]],
    *,
    self_url: str,
    page_url: Callable[[int], str],
) -> FhirSearchBundle:
    _reject_unknown(params, _TERMINOLOGY_SEARCH_PARAMS)
    page = parse_page(params)
    resource_id = _single(params, "_id")
    url = _single(params, "url")
    matches = [
        resource
        for resource in _terminology_resources(export_active_terminology, resource_type)
        if (resource_id is None or resource.id == resource_id)
        and (url is None or resource.url == url)
    ]
    return _search_bundle(
        matches[page.offset : page.offset + page.count],
        total=len(matches),
        page=page,
        self_url=self_url,
        page_url=page_url,
    )


# --- CapabilityStatement ----------------------------------------------------


def capability_statement(*, published: date) -> FhirCapabilityStatement:
    read_search = [
        FhirCapabilityInteraction(code="read"),
        FhirCapabilityInteraction(code="search-type"),
    ]
    paging = [
        FhirCapabilitySearchParam(name="_count", type="number"),
        FhirCapabilitySearchParam(name="_offset", type="number"),
    ]
    patient_ref = [
        FhirCapabilitySearchParam(name="_id", type="token"),
        FhirCapabilitySearchParam(
            name="patient", type="reference", documentation="Patient/<id> or <id>."
        ),
        FhirCapabilitySearchParam(
            name="subject", type="reference", documentation="Same as patient."
        ),
        *paging,
    ]
    resources = [
        FhirCapabilityResource(
            type=ClinicalResourceType.PATIENT.value,
            interaction=read_search,
            searchParam=[FhirCapabilitySearchParam(name="_id", type="token"), *paging],
            operation=[
                FhirCapabilityOperation(
                    name="everything",
                    definition=(
                        "http://hl7.org/fhir/OperationDefinition/Patient-everything"
                    ),
                )
            ],
        ),
        *(
            FhirCapabilityResource(
                type=resource_type.value,
                interaction=read_search,
                searchParam=patient_ref,
            )
            for resource_type in ClinicalResourceType
            if resource_type is not ClinicalResourceType.PATIENT
        ),
        *(
            FhirCapabilityResource(
                type=resource_type.value,
                interaction=read_search,
                searchParam=[
                    FhirCapabilitySearchParam(name="_id", type="token"),
                    FhirCapabilitySearchParam(name="url", type="uri"),
                    *paging,
                ],
            )
            for resource_type in TerminologyResourceType
        ),
    ]
    return FhirCapabilityStatement(
        date=published,
        format=["json"],
        rest=[
            FhirCapabilityRest(
                documentation=(
                    "Read-only, pseudonymized, center-scoped access for "
                    "authenticated users of this node. Terminology resources "
                    "describe the active knowledge-base bundle."
                ),
                resource=resources,
            )
        ],
    )


__all__ = [
    "FHIR_EVERYTHING_MAX_EXAMINATIONS",
    "FHIR_PAGE_DEFAULT",
    "FHIR_PAGE_MAX",
    "ClinicalResourceType",
    "FhirApiError",
    "IssueCode",
    "SearchPage",
    "TerminologyResourceType",
    "capability_statement",
    "parse_clinical_type",
    "parse_page",
    "parse_terminology_type",
    "patient_everything",
    "read_clinical_resource",
    "read_terminology_resource",
    "search_clinical_resources",
    "search_terminology_resources",
]
