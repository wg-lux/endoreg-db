from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import logging
from typing import Protocol

from django.db import IntegrityError, transaction

from endoreg_db.exceptions import (
    DicomArtifactIntegrityError,
    DicomConcurrentImportConflictError,
    DicomImportConflictError,
    DicomImportError,
    DicomManifestValidationError,
    describe_interoperability_error,
)
from endoreg_db.models.interoperability.dicom import (
    DicomExportJob,
    DicomInstance,
    DicomSeries,
    DicomStudy,
)
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.schemas.dicom_export import (
    DicomExportManifestV2,
    validate_dicom_export_manifest_v2,
)
from endoreg_db.utils.structured_logging import emit_structured_event, hash_identifier


logger = logging.getLogger("endoreg_db.interoperability.dicom")


class DicomArtifactVerifier(Protocol):
    def __call__(
        self,
        *,
        artifact_reference: str,
        expected_sha256: str,
        expected_size_bytes: int,
    ) -> bool: ...


@dataclass(frozen=True)
class DicomImportResult:
    export_job: DicomExportJob
    study: DicomStudy
    created: bool


def _emit_import_event(
    event: str,
    *,
    patient_examination_id: object,
    export_id: object | None = None,
    reason: str | None = None,
    level: int = logging.INFO,
) -> None:
    payload: dict[str, str] = {
        "patient_examination_id_sha256": hash_identifier(patient_examination_id),
    }
    if export_id is not None:
        payload["export_id_sha256"] = hash_identifier(export_id)
    if reason is not None:
        payload["reason"] = reason
    emit_structured_event(logger, event, level=level, **payload)


def _emit_import_error(
    error: DicomImportError,
    *,
    patient_examination_id: object,
    export_id: object | None = None,
) -> None:
    descriptor = describe_interoperability_error(error)
    _emit_import_event(
        "dicom.import_rejected",
        patient_examination_id=patient_examination_id,
        export_id=export_id,
        reason=descriptor.log_reason,
        level=logging.ERROR,
    )


def _manifest_digest(manifest: DicomExportManifestV2) -> str:
    canonical = json.dumps(
        manifest.model_dump(mode="json", exclude_none=True),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _verify_artifacts(
    manifest: DicomExportManifestV2,
    verifier: DicomArtifactVerifier,
) -> None:
    for series in manifest.study.series:
        for instance in series.instances:
            if not verifier(
                artifact_reference=instance.artifact_reference,
                expected_sha256=instance.artifact_sha256,
                expected_size_bytes=instance.size_bytes,
            ):
                raise DicomArtifactIntegrityError(
                    "DICOM artifact integrity verification failed for "
                    f"{instance.artifact_reference}"
                )


def _assert_no_uid_conflicts(manifest: DicomExportManifestV2) -> None:
    if DicomStudy.objects.filter(
        study_instance_uid=manifest.study.study_instance_uid
    ).exists():
        raise DicomImportConflictError(
            "Study Instance UID already belongs to an export"
        )

    series_uids = [series.series_instance_uid for series in manifest.study.series]
    if DicomSeries.objects.filter(series_instance_uid__in=series_uids).exists():
        raise DicomImportConflictError(
            "Series Instance UID already belongs to an export"
        )

    sop_uids = [
        instance.sop_instance_uid
        for series in manifest.study.series
        for instance in series.instances
    ]
    if DicomInstance.objects.filter(sop_instance_uid__in=sop_uids).exists():
        raise DicomImportConflictError("SOP Instance UID already belongs to an export")


def import_dicom_export_manifest(
    *,
    patient_examination: PatientExamination,
    payload: Mapping[str, object],
    artifact_verifier: DicomArtifactVerifier,
) -> DicomImportResult:
    """Validate and idempotently catalogue one anonymized DICOM study."""

    if patient_examination.pk is None:
        raise DicomManifestValidationError("patient_examination must be persisted")
    try:
        manifest = validate_dicom_export_manifest_v2(payload)
    except ValueError as exc:
        error = DicomManifestValidationError("DICOM export manifest is invalid")
        _emit_import_error(
            error,
            patient_examination_id=patient_examination.pk,
        )
        raise error from exc
    digest = _manifest_digest(manifest)
    try:
        _verify_artifacts(manifest, artifact_verifier)
    except DicomArtifactIntegrityError as error:
        _emit_import_error(
            error,
            patient_examination_id=patient_examination.pk,
            export_id=manifest.export_id,
        )
        raise

    try:
        with transaction.atomic():
            existing = (
                DicomExportJob.objects.select_for_update()
                .filter(pk=manifest.export_id)
                .first()
            )
            if existing is not None:
                if existing.patient_examination_id != patient_examination.pk:
                    raise DicomImportConflictError(
                        "export_id belongs to another patient examination"
                    )
                if existing.manifest_sha256 != digest:
                    raise DicomImportConflictError(
                        "export_id was reused with different manifest content"
                    )
                if existing.status != DicomExportJob.Status.IMPORTED:
                    raise DicomImportConflictError(
                        f"existing export is in non-imported status {existing.status}"
                    )
                result = DicomImportResult(
                    export_job=existing,
                    study=existing.study,
                    created=False,
                )
            else:
                _assert_no_uid_conflicts(manifest)
                manifest_json = manifest.model_dump(mode="json", exclude_none=True)
                export_job = DicomExportJob.objects.create(
                    id=manifest.export_id,
                    patient_examination=patient_examination,
                    status=DicomExportJob.Status.RECEIVED,
                    source_system=manifest.source_system,
                    schema_version=manifest.schema_version,
                    manifest_sha256=digest,
                    manifest=manifest_json,
                )
                study = DicomStudy.objects.create(
                    export_job=export_job,
                    patient_examination=patient_examination,
                    study_instance_uid=manifest.study.study_instance_uid,
                    patient_pseudonym=manifest.study.patient_pseudonym,
                    accession_identifier=manifest.study.accession_identifier,
                    study_date=manifest.study.study_date,
                )
                for series_item in manifest.study.series:
                    series = DicomSeries.objects.create(
                        study=study,
                        series_instance_uid=series_item.series_instance_uid,
                        modality=series_item.modality,
                        series_number=series_item.series_number,
                    )
                    DicomInstance.objects.bulk_create(
                        [
                            DicomInstance(
                                series=series,
                                sop_instance_uid=item.sop_instance_uid,
                                sop_class_uid=item.sop_class_uid,
                                transfer_syntax_uid=item.transfer_syntax_uid,
                                instance_number=item.instance_number,
                                artifact_reference=item.artifact_reference,
                                artifact_class=item.artifact_class,
                                artifact_sha256=item.artifact_sha256,
                                size_bytes=item.size_bytes,
                                masked_regions=item.masked_regions,
                            )
                            for item in series_item.instances
                        ]
                    )
                export_job.status = DicomExportJob.Status.IMPORTED
                export_job.save(update_fields=["status", "updated_at"])
                result = DicomImportResult(
                    export_job=export_job,
                    study=study,
                    created=True,
                )
    except DicomImportConflictError as error:
        _emit_import_error(
            error,
            patient_examination_id=patient_examination.pk,
            export_id=manifest.export_id,
        )
        raise
    except IntegrityError as exc:
        error = DicomConcurrentImportConflictError(
            "concurrent DICOM import created an identity conflict"
        )
        _emit_import_error(
            error,
            patient_examination_id=patient_examination.pk,
            export_id=manifest.export_id,
        )
        raise error from exc

    _emit_import_event(
        "dicom.import_completed" if result.created else "dicom.import_replayed",
        patient_examination_id=patient_examination.pk,
        export_id=manifest.export_id,
    )
    return result


__all__ = [
    "DicomArtifactIntegrityError",
    "DicomArtifactVerifier",
    "DicomImportConflictError",
    "DicomManifestValidationError",
    "DicomImportResult",
    "import_dicom_export_manifest",
]
