"""Atomic table import using canonical identity, clinical and media services."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time
from pathlib import Path
from typing import cast
from uuid import uuid5

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone
from lx_dtypes.models.contracts.dtypes_record_persistence import (
    DtypesRecordPersistencePayload,
)
from lx_dtypes.models.contracts.table_import import (
    TableExtractionError,
    TableExtractor,
    TableImportReceiptPayload,
    TableImportResult,
    TableMapping,
    TableRecord,
)

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.medical.examination.examination import Examination
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.models.other.gender import Gender
from endoreg_db.models.other.study_table_import import StudyTableImport
from endoreg_db.services.cases.auto_resolution import link_media_to_patient_examination
from endoreg_db.services.centers.access import resolve_allowed_center_ids
from endoreg_db.services.reports.finding_sync import sync_report_findings
from endoreg_db.services.reports.structured_records import (
    persist_patient_examination_dtypes_record_from_ledger,
)
from endoreg_db.services.studies.table_parser import (
    MAX_TABLE_BYTES,
    extract_table_records,
    load_table_mapping,
)
from endoreg_db.utils.hashs import get_file_hash


def _identity(row: TableRecord, center: Center) -> SensitiveMeta:
    if row.patient.dob is None:
        raise ValueError("Complete identity required")
    gender = Gender.objects.get(name=row.patient.gender)
    return SensitiveMeta(
        patient_first_name=row.patient.first_name,
        patient_last_name=row.patient.last_name,
        patient_dob=timezone.make_aware(datetime.combine(row.patient.dob, time.min)),
        patient_gender=gender,
        center=center,
        examination_date=row.examination_date,
    )


def _scoped_record(
    row: TableRecord, owner: User, center: Center
) -> DtypesRecordPersistencePayload:
    record = row.record.model_copy(deep=True)
    namespace = uuid5(row.source_id, f"{owner.pk}:{center.pk}")
    record.uuid = namespace
    for finding in record.patient_findings:
        finding.uuid = uuid5(namespace, str(finding.uuid))
        for group in finding.patient_finding_classifications:
            group.uuid = finding.uuid
            group.patient_finding = str(finding.uuid)
            for choice in group.patient_finding_classification_choices:
                choice.uuid = uuid5(namespace, str(choice.uuid))
                choice.patient_finding_classifications = str(group.uuid)
    return record


def _finding_payload(
    record: DtypesRecordPersistencePayload,
) -> list[Mapping[str, object]]:
    return [
        {
            "finding": finding.finding,
            "instance_id": str(finding.uuid),
            "classifications": [
                {
                    "classification": choice.classification,
                    "classification_choice": choice.classification_choice,
                }
                for group in finding.patient_finding_classifications
                for choice in group.patient_finding_classification_choices
            ],
        }
        for finding in record.patient_findings
    ]


def _match_media(
    meta: SensitiveMeta, examination: PatientExamination
) -> tuple[int, int]:
    counts: list[int] = []
    for model, kind in ((VideoFile, "video"), (RawPdfFile, "pdf")):
        media_rows = model.objects.select_for_update().filter(
            center_id=meta.center_id,
            sensitive_meta__center_id=meta.center_id,
            sensitive_meta__patient_hash=meta.patient_hash,
            sensitive_meta__examination_hash=meta.examination_hash,
        )
        count = 0
        for media in media_rows:
            if media.patient_id not in (
                None,
                examination.patient_id,
            ) or media.examination_id not in (None, examination.pk):
                raise ValueError(
                    "Existing media identity conflicts with the imported examination"
                )
            link_media_to_patient_examination(
                media_type="video" if kind == "video" else "pdf",
                media_obj=media,
                patient_examination=examination,
            )
            count += 1
        counts.append(count)
    return counts[0], counts[1]


def _persist_row(
    row: TableRecord,
    spec: TableMapping,
    *,
    owner: User,
    center: Center,
    mapping_hash: str,
    source_hash: str,
) -> tuple[PatientExamination, bool, int, int]:
    meta = _identity(row, center)
    patient_hash = meta.get_patient_hash()
    examination_hash = meta.get_patient_examination_hash()
    record = _scoped_record(row, owner, center)
    existing = (
        StudyTableImport.objects.filter(
            owner=owner,
            center=center,
            mapping_id=spec.mapping_id,
            source_id=row.source_id,
        )
        .select_related("patient_examination")
        .first()
    )
    if existing is not None:
        previous_examination = existing.patient_examination
        if previous_examination is None:
            raise ValueError(
                "Source row was explicitly deleted; a new mapping identity is required"
            )
        if (
            previous_examination.patient.patient_hash != patient_hash
            or previous_examination.patient.center_id != center.pk
            or previous_examination.hash != examination_hash
        ):
            raise ValueError("Imported examination identity has changed")
        record.patient = str(previous_examination.patient_id)
        receipt = TableImportReceiptPayload(
            patient_hash=patient_hash, examination_hash=examination_hash, record=record
        )
        if (
            existing.mapping_hash != mapping_hash
            or TableImportReceiptPayload.model_validate(existing.payload).model_dump(
                mode="json"
            )
            != receipt.model_dump(mode="json")
        ):
            raise ValueError("Existing source row has different content")
        meta.patient_hash = patient_hash
        meta.examination_hash = examination_hash
        videos, reports = _match_media(meta, previous_examination)
        return previous_examination, True, videos, reports

    examination_type = Examination.objects.get(name=record.examination)
    # SensitiveMeta owns salt validation, collision detection, identity rotation,
    # patient creation and examination identity. Never duplicate that algorithm.
    owns_examination = not PatientExamination.objects.filter(
        hash=examination_hash
    ).exists()
    meta.save()
    examination = meta.pseudo_examination
    if examination is None or meta.pseudo_patient is None or not meta.patient_hash:
        raise ValueError(
            "Complete sensitive metadata did not resolve a patient examination"
        )
    examination = PatientExamination.objects.select_for_update().get(pk=examination.pk)
    if examination.examination_id not in (None, examination_type.pk):
        raise ValueError("Existing examination has a different clinical type")
    identity = spec.knowledge_base
    if examination.knowledge_base_module and (
        examination.knowledge_base_module != identity.knowledge_base_module
        or examination.knowledge_base_version != identity.knowledge_base_version
    ):
        raise ValueError("Existing examination uses a different knowledge base")
    examination.examination = examination_type
    examination.knowledge_base_module = identity.knowledge_base_module
    examination.knowledge_base_version = identity.knowledge_base_version
    existing_date: date | None = examination.date_start
    if existing_date is None:
        examination.date_start = row.examination_date
    elif existing_date != row.examination_date:
        raise ValueError("Existing examination date conflicts with source")
    examination.save()
    record.patient = str(examination.patient_id)
    sync_report_findings(
        examination, _finding_payload(record), user=owner, replace=False
    )
    persist_patient_examination_dtypes_record_from_ledger(examination)
    videos, reports = _match_media(meta, examination)
    receipt = TableImportReceiptPayload(
        patient_hash=patient_hash, examination_hash=examination_hash, record=record
    )
    StudyTableImport.objects.create(
        owner=owner,
        center=center,
        patient_examination=examination,
        mapping_id=spec.mapping_id,
        source_id=row.source_id,
        mapping_hash=mapping_hash,
        source_hash=source_hash,
        payload=receipt.model_dump(mode="json"),
        owns_examination=owns_examination,
        deletion_record=examination.dtypes_record,
    )
    return examination, False, videos, reports


def import_table(
    mapping_file: Path,
    source_file: Path,
    *,
    owner: User,
    center: Center,
    dry_run: bool = True,
    extractors: Mapping[str, TableExtractor] | None = None,
) -> TableImportResult:
    """Parse, validate and atomically persist a complete table (dry-run by default).

    Trusted callers supply local files. The HTTP adapter only accepts uploads
    and an owned mapping UUID. Callers add cohort links in the same transaction.
    """
    if not owner.is_authenticated or owner.pk is None:
        raise PermissionError("An authenticated owner is required")
    allowed = resolve_allowed_center_ids(owner)
    if center.pk is None or (allowed is not None and center.pk not in allowed):
        raise PermissionError("Center access denied")
    if source_file.stat().st_size > MAX_TABLE_BYTES:
        raise ValueError("Table exceeds 20 MiB")
    mapping_hash, source_hash = get_file_hash(mapping_file), get_file_hash(source_file)
    spec = load_table_mapping(mapping_file)
    with source_file.open("rb") as source:
        content = source.read(MAX_TABLE_BYTES + 1)
    rows = extract_table_records(
        spec, content, source_file.suffix.lower(), extractors=extractors
    )
    if (mapping_hash, source_hash) != (
        get_file_hash(mapping_file),
        get_file_hash(source_file),
    ):
        raise ValueError("Input changed during validation")
    examinations: set[int] = set()
    replayed = videos = reports = 0
    with transaction.atomic():
        # Serialize import writers for this center, including the first receipt.
        center = Center.objects.select_for_update().get(pk=center.pk)
        for row in rows:
            try:
                examination, reused, matched_videos, matched_reports = _persist_row(
                    row,
                    spec,
                    owner=owner,
                    center=center,
                    mapping_hash=mapping_hash,
                    source_hash=source_hash,
                )
            except (ValueError, Gender.DoesNotExist, Examination.DoesNotExist) as error:
                raise TableExtractionError(
                    row.row_number,
                    "persistence",
                    "identity_terminology_or_reimport_conflict",
                ) from error
            if examination.pk not in examinations:
                videos += matched_videos
                reports += matched_reports
            examinations.add(cast(int, examination.pk))
            replayed += int(reused)
        if dry_run:
            transaction.set_rollback(True)
    return TableImportResult(
        row_count=len(rows),
        created_count=len(rows) - replayed,
        replayed_count=replayed,
        patient_examination_ids=[] if dry_run else sorted(examinations),
        matched_video_count=videos,
        matched_report_count=reports,
        dry_run=dry_run,
    )
