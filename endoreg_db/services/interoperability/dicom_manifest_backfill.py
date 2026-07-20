from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from endoreg_db.exceptions import DicomManifestBackfillError
from endoreg_db.models.interoperability.dicom import DicomExportJob
from endoreg_db.schemas.dicom_export import (
    DICOM_EXPORT_MANIFEST_SCHEMA_VERSION,
    UnsupportedDicomManifestVersionError,
    dicom_export_manifest_sha256,
    dump_dicom_export_manifest_v2,
)
from endoreg_db.utils.structured_logging import hash_identifier


@dataclass(frozen=True, slots=True)
class DicomManifestBackfillResult:
    scanned: int
    current: int
    would_update: int
    updated: int
    applied: bool
    schema_version: int = DICOM_EXPORT_MANIFEST_SCHEMA_VERSION


def backfill_dicom_export_manifests_v2(
    *,
    apply: bool = False,
) -> DicomManifestBackfillResult:
    """Validate and canonicalize persisted V2 manifests in one transaction.

    Dry-run is the default. Apply mode locks all selected rows and rolls the
    complete cohort back if any record is invalid or uses an unsupported schema.
    """

    scanned = 0
    current = 0
    would_update = 0
    updated = 0

    with transaction.atomic():
        export_jobs = DicomExportJob.objects.order_by("created_at", "pk")
        if apply:
            export_jobs = export_jobs.select_for_update()

        for export_job in export_jobs.iterator():
            scanned += 1
            record_reference = hash_identifier(export_job.pk)
            try:
                canonical_manifest = dump_dicom_export_manifest_v2(export_job.manifest)
            except UnsupportedDicomManifestVersionError as exc:
                raise DicomManifestBackfillError(
                    f"DICOM manifest record {record_reference} has {exc}"
                ) from exc
            except ValueError as exc:
                raise DicomManifestBackfillError(
                    f"DICOM manifest record {record_reference} failed V2 validation"
                ) from exc

            if str(canonical_manifest["export_id"]) != str(export_job.pk):
                raise DicomManifestBackfillError(
                    f"DICOM manifest record {record_reference} has an export_id mismatch"
                )

            canonical_digest = dicom_export_manifest_sha256(canonical_manifest)
            needs_update = (
                export_job.schema_version != DICOM_EXPORT_MANIFEST_SCHEMA_VERSION
                or export_job.manifest != canonical_manifest
                or export_job.manifest_sha256 != canonical_digest
            )
            if not needs_update:
                current += 1
                continue

            would_update += 1
            if not apply:
                continue
            DicomExportJob.objects.filter(pk=export_job.pk).update(
                schema_version=DICOM_EXPORT_MANIFEST_SCHEMA_VERSION,
                manifest=canonical_manifest,
                manifest_sha256=canonical_digest,
                updated_at=timezone.now(),
            )
            updated += 1

    return DicomManifestBackfillResult(
        scanned=scanned,
        current=current,
        would_update=would_update,
        updated=updated,
        applied=apply,
    )


__all__ = [
    "DicomManifestBackfillError",
    "DicomManifestBackfillResult",
    "backfill_dicom_export_manifests_v2",
]
