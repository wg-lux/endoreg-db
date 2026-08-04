from __future__ import annotations

import hashlib
import logging
import re
from enum import StrEnum

from django.db.models import Prefetch

from endoreg_db.exceptions import (
    FhirExportValidationError,
    describe_interoperability_error,
)
from endoreg_db.models.interoperability.dicom import DicomSeries, DicomStudy
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.medical.patient.patient_finding import PatientFinding
from endoreg_db.models.medical.patient.patient_finding_classification import (
    PatientFindingClassification,
)
from endoreg_db.models.report.patient_examination_report import (
    PatientExaminationReport,
)
from endoreg_db.schemas.fhir_r4 import (
    FhirBundle,
    FhirBundleEntry,
    FhirCodeableConcept,
    FhirCoding,
    FhirDiagnosticReport,
    FhirIdentifier,
    FhirImagingStudy,
    FhirImagingStudySeries,
    FhirMeta,
    FhirObservation,
    FhirObservationComponent,
    FhirPatient,
    FhirPeriod,
    FhirProcedure,
    FhirReference,
)
from endoreg_db.utils.structured_logging import emit_structured_event, hash_identifier


FHIR_BASE_URL = "https://wg-lux.de/fhir"
ENDOREG_IDENTIFIER_SYSTEM = f"{FHIR_BASE_URL}/sid/endoreg-db"
EXAMINATION_CODE_SYSTEM = f"{FHIR_BASE_URL}/CodeSystem/lx-examination-cs"
FINDING_CODE_SYSTEM = f"{FHIR_BASE_URL}/CodeSystem/lx-finding-cs"
CLASSIFICATION_CODE_SYSTEM = f"{FHIR_BASE_URL}/CodeSystem/lx-classification-cs"
CLASSIFICATION_CHOICE_CODE_SYSTEM = (
    f"{FHIR_BASE_URL}/CodeSystem/lx-classification-choice-cs"
)
DICOM_UID_SYSTEM = "urn:dicom:uid"
DICOM_MODALITY_SYSTEM = "http://dicom.nema.org/resources/ontology/DCM"
FHIR_EXPORT_PROFILE_URL = (
    f"{FHIR_BASE_URL}/StructureDefinition/lx-pseudonymized-endoscopy-bundle"
)
FHIR_EXPORT_VERSION_SYSTEM = f"{FHIR_BASE_URL}/CodeSystem/lx-export-version"
FHIR_EXPORT_VERSION = "1.0"

logger = logging.getLogger("endoreg_db.interoperability.fhir")


class FhirExportProfile(StrEnum):
    PSEUDONYMIZED = "pseudonymized"


def _fhir_id(prefix: str, source_identity: object) -> str:
    identity = str(source_identity).strip()
    if not identity:
        raise ValueError("FHIR resource source identity must not be empty")
    digest = hashlib.sha256(f"{prefix}:{identity}".encode()).hexdigest()
    return f"{prefix}-{digest[: 63 - len(prefix)]}"


def _patient_pseudonym(examination: PatientExamination) -> str:
    patient_hash = (examination.patient.patient_hash or "").strip()
    if not patient_hash:
        raise ValueError("pseudonymized FHIR export requires patient_hash")
    return patient_hash


def _patient_resource_id(examination: PatientExamination) -> str:
    return _fhir_id("patient", _patient_pseudonym(examination))


def _procedure_resource_id(examination: PatientExamination) -> str:
    return _fhir_id("procedure", examination.hash)


def _code(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9.-]+", "-", value.strip().lower()).strip("-")
    if not normalized:
        raise ValueError("terminology name cannot be converted to a FHIR code")
    return normalized


def _reference(resource_type: str, resource_id: str) -> FhirReference:
    return FhirReference(reference=f"{resource_type}/{resource_id}")


def _entry(
    resource: FhirPatient
    | FhirProcedure
    | FhirObservation
    | FhirDiagnosticReport
    | FhirImagingStudy,
) -> FhirBundleEntry:
    return FhirBundleEntry(
        fullUrl=f"{resource.resource_type}/{resource.id}",
        resource=resource,
    )


def _patient_resource(examination: PatientExamination) -> FhirPatient:
    patient = examination.patient
    if patient.pk is None:
        raise ValueError("patient must be persisted")
    patient_pseudonym = _patient_pseudonym(examination)
    return FhirPatient(
        id=_patient_resource_id(examination),
        identifier=[
            FhirIdentifier(
                system=f"{ENDOREG_IDENTIFIER_SYSTEM}/patient-pseudonym-sha256",
                value=hash_identifier(patient_pseudonym),
            )
        ],
    )


def _procedure_resource(examination: PatientExamination) -> FhirProcedure:
    if examination.pk is None or examination.patient_id is None:
        raise ValueError("patient examination must be persisted with a patient")
    examination_definition = examination.examination
    concept = (
        FhirCodeableConcept(
            coding=[
                FhirCoding(
                    system=EXAMINATION_CODE_SYSTEM,
                    code=_code(examination_definition.name),
                    display=examination_definition.name,
                )
            ],
            text=examination_definition.description or examination_definition.name,
        )
        if examination_definition is not None
        else FhirCodeableConcept(text="Unspecified endoscopic examination")
    )
    return FhirProcedure(
        id=_procedure_resource_id(examination),
        status="completed" if examination.date_end is not None else "unknown",
        code=concept,
        subject=_reference("Patient", _patient_resource_id(examination)),
        performedPeriod=FhirPeriod(
            start=examination.date_start,
            end=examination.date_end,
        )
        if examination.date_start is not None or examination.date_end is not None
        else None,
    )


def _observation_resources(examination: PatientExamination) -> list[FhirObservation]:
    if examination.pk is None or examination.patient_id is None:
        raise ValueError("patient examination must be persisted with a patient")
    findings = PatientFinding.objects.filter(
        patient_examination=examination,
        is_active=True,
    ).select_related("finding")
    classifications = PatientFindingClassification.objects.filter(
        finding__in=findings,
        is_active=True,
    ).select_related("classification", "classification_choice")
    classifications_by_finding: dict[int, list[PatientFindingClassification]] = {}
    for classification in classifications:
        finding_id = classification.finding.pk
        if finding_id is None:
            raise ValueError("classified patient finding must be persisted")
        classifications_by_finding.setdefault(finding_id, []).append(classification)

    observations: list[FhirObservation] = []
    for finding in findings:
        if finding.pk is None:
            raise ValueError("patient finding must be persisted")
        components = [
            FhirObservationComponent(
                code=FhirCodeableConcept(
                    coding=[
                        FhirCoding(
                            system=CLASSIFICATION_CODE_SYSTEM,
                            code=_code(item.classification.name),
                            display=item.classification.name,
                        )
                    ]
                ),
                valueCodeableConcept=FhirCodeableConcept(
                    coding=[
                        FhirCoding(
                            system=CLASSIFICATION_CHOICE_CODE_SYSTEM,
                            code=_code(item.classification_choice.name),
                            display=item.classification_choice.name,
                        )
                    ]
                ),
            )
            for item in classifications_by_finding.get(finding.pk, [])
        ]
        observations.append(
            FhirObservation(
                id=_fhir_id("observation", f"{examination.hash}:{finding.pk}"),
                status="preliminary",
                code=FhirCodeableConcept(
                    coding=[
                        FhirCoding(
                            system=FINDING_CODE_SYSTEM,
                            code=_code(finding.finding.name),
                            display=finding.finding.name,
                        )
                    ],
                    text=finding.finding.description or finding.finding.name,
                ),
                subject=_reference("Patient", _patient_resource_id(examination)),
                partOf=[_reference("Procedure", _procedure_resource_id(examination))],
                effectiveDateTime=examination.date_start,
                component=components,
            )
        )
    return observations


def _imaging_study_resources(
    examination: PatientExamination,
) -> list[FhirImagingStudy]:
    if examination.pk is None or examination.patient_id is None:
        raise ValueError("patient examination must be persisted with a patient")
    studies = DicomStudy.objects.filter(
        patient_examination=examination,
        export_job__status="imported",
    ).prefetch_related(
        Prefetch(
            "series",
            queryset=DicomSeries.objects.prefetch_related("instances"),
        )
    )
    resources: list[FhirImagingStudy] = []
    for study in studies:
        if study.pk is None:
            raise ValueError("DICOM study must be persisted")
        series_resources = [
            FhirImagingStudySeries(
                uid=series.series_instance_uid,
                number=series.series_number,
                modality=FhirCoding(
                    system=DICOM_MODALITY_SYSTEM,
                    code=series.modality,
                    display=series.modality,
                ),
                numberOfInstances=series.instances.count(),
            )
            for series in study.series.all()
        ]
        resources.append(
            FhirImagingStudy(
                id=_fhir_id("imagingstudy", study.study_instance_uid),
                identifier=[
                    FhirIdentifier(
                        system=DICOM_UID_SYSTEM,
                        value=f"urn:oid:{study.study_instance_uid}",
                    )
                ],
                status="available",
                subject=_reference("Patient", _patient_resource_id(examination)),
                started=(
                    study.study_date
                    if study.study_date is not None
                    else examination.date_start
                ),
                numberOfSeries=len(series_resources),
                numberOfInstances=sum(
                    item.number_of_instances for item in series_resources
                ),
                series=series_resources,
            )
        )
    return resources


def _diagnostic_report_resources(
    examination: PatientExamination,
    observations: list[FhirObservation],
    imaging_studies: list[FhirImagingStudy],
) -> list[FhirDiagnosticReport]:
    if examination.patient_id is None:
        raise ValueError("patient examination must have a patient")
    reports = PatientExaminationReport.objects.filter(
        patient_examination=examination,
        is_active=True,
    ).order_by("id")
    return [
        FhirDiagnosticReport(
            id=_fhir_id(
                "diagnosticreport",
                f"{examination.hash}:{report.pk}",
            ),
            status=(
                "final"
                if report.status == PatientExaminationReport.Status.FINAL
                else "preliminary"
            ),
            code=FhirCodeableConcept(
                text=report.title or report.template_name,
            ),
            subject=_reference("Patient", _patient_resource_id(examination)),
            result=[
                _reference("Observation", observation.id)
                for observation in observations
            ],
            imagingStudy=[
                _reference("ImagingStudy", imaging_study.id)
                for imaging_study in imaging_studies
            ],
        )
        for report in reports
        if report.pk is not None
    ]


def build_patient_examination_fhir_bundle(
    examination: PatientExamination,
    *,
    profile: FhirExportProfile = FhirExportProfile.PSEUDONYMIZED,
) -> FhirBundle:
    """Build and validate one read-only, pseudonymized FHIR R4 Bundle."""

    examination_log_id = hash_identifier(
        examination.pk if examination.pk is not None else "unpersisted"
    )
    try:
        if examination.pk is None or examination.patient_id is None:
            raise ValueError("patient examination must be persisted with a patient")
        patient = _patient_resource(examination)
        procedure = _procedure_resource(examination)
        observations = _observation_resources(examination)
        imaging_studies = _imaging_study_resources(examination)
        reports = _diagnostic_report_resources(
            examination,
            observations,
            imaging_studies,
        )
        entries = [
            _entry(patient),
            _entry(procedure),
            *(_entry(item) for item in observations),
            *(_entry(item) for item in imaging_studies),
            *(_entry(item) for item in reports),
        ]
        bundle = FhirBundle(
            id=_fhir_id("bundle", examination.hash),
            meta=FhirMeta(
                profile=[FHIR_EXPORT_PROFILE_URL],
                tag=[
                    FhirCoding(
                        system=FHIR_EXPORT_VERSION_SYSTEM,
                        code=FHIR_EXPORT_VERSION,
                    )
                ],
            ),
            identifier=FhirIdentifier(
                system=f"{ENDOREG_IDENTIFIER_SYSTEM}/examination-pseudonym-sha256",
                value=hash_identifier(examination.hash),
            ),
            entry=entries,
        )
    except ValueError as exc:
        error = FhirExportValidationError(
            "persisted examination data violates the FHIR export contract"
        )
        descriptor = describe_interoperability_error(error)
        emit_structured_event(
            logger,
            "fhir.export_rejected",
            level=logging.ERROR,
            patient_examination_id_sha256=examination_log_id,
            export_profile=profile.value,
            reason=descriptor.log_reason,
            error_code=descriptor.code.value,
            error_type=exc.__class__.__name__,
        )
        raise error from exc
    except Exception as exc:
        emit_structured_event(
            logger,
            "fhir.export_rejected",
            level=logging.ERROR,
            patient_examination_id_sha256=examination_log_id,
            export_profile=profile.value,
            reason="unexpected_error",
            error_type=exc.__class__.__name__,
        )
        raise

    emit_structured_event(
        logger,
        "fhir.export_completed",
        patient_examination_id_sha256=examination_log_id,
        export_profile=profile.value,
        bundle_id=bundle.id,
        resource_count=len(bundle.entry),
    )
    return bundle


__all__ = ["FhirExportProfile", "build_patient_examination_fhir_bundle"]
