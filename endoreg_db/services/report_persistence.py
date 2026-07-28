from __future__ import annotations

import hashlib
import re
import tempfile
from dataclasses import dataclass
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
from pathlib import Path
from typing import Protocol, cast

from django.contrib.auth import get_user_model
from django.contrib.auth.models import User as AuthUser
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.pdf.raw_pdf import ReportMetaJsonObject
from endoreg_db.models.media.pdf.report_file import AnonymExaminationReport
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.medical.patient.patient_examination_indication import (
    PatientExaminationIndication,
)
from endoreg_db.models.other.gender import Gender
from endoreg_db.models.report.patient_examination_report import PatientExaminationReport
from endoreg_db.schemas import validate_raw_pdf_meta_payload
from endoreg_db.services.dtypes_records import (
    persist_patient_examination_dtypes_record_from_ledger,
)
from endoreg_db.services.report_finding_sync import (
    parse_report_date,
    sync_report_findings,
)
from endoreg_db.services.report_history import get_patient_examination_history_context
from lx_dtypes.models.contracts.patient_examination_report import (
    report_json_safe_dict,
)

User = get_user_model()


class _IdentifiedLike(Protocol):
    id: int


class _PatientContextLike(Protocol):
    dob: date | None
    first_name: str
    last_name: str
    gender_id: int | None
    gender: Gender | None
    center_id: int | None
    center: Center | None

    def save(self, *args: object, **kwargs: object) -> None: ...


class _WritableFileLike(Protocol):
    def save(
        self, name: str, content: ContentFile[bytes], save: bool = True
    ) -> None: ...


class _PatientExaminationReportLike(Protocol):
    id: int
    template_name: str
    template_version: str
    template_hash: str
    title: str
    status: str
    editor_payload: Mapping[str, object]
    patient_context_snapshot: Mapping[str, object]
    history_context_snapshot: Mapping[str, object]
    rendered_text: str
    version: int
    patient_examination: PatientExamination
    created_by: AuthUser | None
    updated_by: AuthUser | None
    finalized_at: datetime | None
    finalized_by: AuthUser | None

    def save(self, *args: object, **kwargs: object) -> None: ...


class _AnonymExaminationReportLike(Protocol):
    pk: int | None
    patient_examination: PatientExamination | None
    patient: Patient | None
    center: Center | None
    text: str | None
    meta: Mapping[str, object] | None
    date: date | None
    time: time | None
    file: _WritableFileLike

    def save(self, *args: object, **kwargs: object) -> None: ...


class _RawPdfFileLike(Protocol):
    pk: int | None
    pdf_hash: str
    patient: Patient | None
    examination: PatientExamination | None
    center: Center | None
    text: str | None
    raw_meta: ReportMetaJsonObject | None
    anonym_examination_report: AnonymExaminationReport | None
    file: _WritableFileLike

    def save(self, *args: object, **kwargs: object) -> None: ...


@dataclass(slots=True)
class SaveReportSubmissionResult:
    report: PatientExaminationReport
    created: bool
    warnings: list[str]
    history_context: dict[str, object]
    persisted_dtypes_record: dict[str, object] | None = None
    persisted_dtypes_record_updated_at: datetime | None = None
    persisted_report_artifact_id: int | None = None
    persisted_pdf_artifact_id: int | None = None


@dataclass(frozen=True, slots=True)
class _FindingsSyncResult:
    warnings: list[str]
    persisted_record: dict[str, object] | None
    persisted_record_updated_at: datetime | None


def _resolve_gender(value: object) -> Gender | None:
    if value in (None, ""):
        return None
    if isinstance(value, int):
        return Gender.objects.filter(pk=value).first()
    if isinstance(value, str):
        return Gender.objects.filter(name=value).first()
    return None


def _resolve_center(value: object) -> Center | None:
    if value in (None, ""):
        return None
    if isinstance(value, int):
        return Center.objects.filter(pk=value).first()
    if isinstance(value, str):
        return Center.objects.filter(name=value).first()
    return None


def _normalize_pdf_meta_payload(
    value: dict[str, object],
) -> ReportMetaJsonObject:
    validated_pdf_meta = validate_raw_pdf_meta_payload(value)
    return cast(ReportMetaJsonObject, validated_pdf_meta or {})


def _safe_file_component(value: str, *, fallback: str = "report") -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", (value or "").strip()).strip("._")
    return cleaned or fallback


def _escape_pdf_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _render_minimal_pdf_bytes(*, title: str, body_text: str) -> bytes:
    """
    Render a tiny valid single-page PDF with plain text content.

    Fallback renderer used when no PDF engine (e.g. WeasyPrint) is installed.
    """
    title = (title or "Report").strip()
    body_text = body_text or ""
    lines = [title] + [ln for ln in body_text.splitlines() if ln.strip()]
    if not lines:
        lines = ["Report"]

    y = 780
    content_ops: list[str] = ["BT", "/F1 12 Tf"]
    for line in lines[:60]:
        content_ops.append(f"72 {y} Td ({_escape_pdf_text(line[:180])}) Tj")
        y -= 14
        if y < 72:
            break
    content_ops.append("ET")
    content_stream = "\n".join(content_ops).encode("latin-1", errors="replace")

    objects: list[bytes] = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    objects.append(
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
    )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objects.append(
        b"<< /Length "
        + str(len(content_stream)).encode("ascii")
        + b" >>\nstream\n"
        + content_stream
        + b"\nendstream"
    )

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for idx, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f"{idx} 0 obj\n".encode("ascii"))
        out.extend(obj)
        out.extend(b"\nendobj\n")

    xref_pos = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    out.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(out)


def persist_report_pdf_artifact(
    report: PatientExaminationReport,
    patient_examination: PatientExamination,
    *,
    rendered_text: str = "",
    section_blocks: Sequence[Mapping[str, object]] | None = None,
    frame_image_paths: list[str] | None = None,
    frame_captions: list[str] | None = None,
    patient_identity: Mapping[str, object] | None = None,
    strict_renderer: bool = False,
) -> tuple[int | None, int | None]:
    """
    Create/update linked full-report + pdf media artifacts for a persisted report.

    Returns:
        (anonym_examination_report_id, raw_pdf_file_id)
    """
    report_ref = cast(_PatientExaminationReportLike, report)
    patient_obj = patient_examination.patient
    assert patient_obj is not None, (
        "PatientExamination must have an associated patient."
    )
    patient = patient_obj
    patient_ref = cast(_PatientContextLike, patient)
    patient_examination_ref = cast(_IdentifiedLike, patient_examination)
    center = patient_ref.center
    report_date = patient_examination.date_start

    report_id = report_ref.id
    patient_examination_id = patient_examination_ref.id
    report_title = report_ref.title or f"{report_ref.template_name} report"
    pdf_body = rendered_text or report_ref.rendered_text or ""
    pdf_meta_input: dict[str, object] = {
        "source": "patient_examination_report",
        "patient_examination_report_id": report_id,
        "template_name": report_ref.template_name,
        "template_version": report_ref.template_version,
        "template_hash": report_ref.template_hash,
        "version": report_ref.version,
        "status": report_ref.status,
        "generated_at": timezone.now().isoformat(),
    }
    if report_ref.editor_payload:
        pdf_meta_input["editor_payload"] = report_ref.editor_payload
    pdf_meta = _normalize_pdf_meta_payload(pdf_meta_input)
    renderer_section_blocks = (
        None if section_blocks is None else [dict(block) for block in section_blocks]
    )
    renderer_patient_identity = (
        None if patient_identity is None else dict(patient_identity)
    )

    pdf_bytes: bytes
    try:
        from endoreg_db.services.report_pdf_renderer import (
            build_report_template_pdf_payload,
            render_pdf_with_rust_renderer,
        )

        payload = build_report_template_pdf_payload(
            report=report,
            patient_examination=patient_examination,
            section_blocks=renderer_section_blocks,
            frame_image_paths=frame_image_paths,
            frame_captions=frame_captions,
            patient_identity=renderer_patient_identity,
        )
        with tempfile.TemporaryDirectory(prefix="endoreg_report_pdf_") as tmp_dir:
            out_path = Path(tmp_dir) / "report.pdf"
            render_pdf_with_rust_renderer(payload, output_path=out_path)
            pdf_bytes = out_path.read_bytes()
    except Exception:
        if strict_renderer:
            raise
        pdf_bytes = _render_minimal_pdf_bytes(
            title=report_title,
            body_text=pdf_body,
        )
    pdf_hash = hashlib.sha256(pdf_bytes).hexdigest()

    full_report = AnonymExaminationReport.objects.filter(
        meta__patient_examination_report_id=report_id
    ).first()
    if full_report is None:
        full_report = AnonymExaminationReport(
            patient_examination=patient_examination,
            patient=patient,
            center=center,
        )
    full_report_ref = cast(_AnonymExaminationReportLike, full_report)
    full_report_ref.patient = patient
    full_report_ref.center = center
    full_report_ref.text = pdf_body
    full_report_ref.meta = pdf_meta
    full_report_ref.date = report_date
    full_report_ref.time = None

    pdf_filename = _safe_file_component(
        f"report_{patient_examination_id}_r{report_id}_v{report_ref.version}.pdf",
        fallback=f"report_{report_id}.pdf",
    )

    # Keep full report file in sync too (optional but useful for timeline/display)
    full_report_ref.file.save(pdf_filename, ContentFile[bytes](pdf_bytes), save=False)
    full_report_ref.save()

    raw_pdf = RawPdfFile.objects.filter(anonym_examination_report=full_report).first()
    raw_pdf_is_new = raw_pdf is None
    if raw_pdf is None:
        raw_pdf = RawPdfFile(
            pdf_hash=pdf_hash,
            patient=patient,
            examination=patient_examination,
            center=center,
            text=pdf_body,
            raw_meta=pdf_meta,
            anonym_examination_report=full_report,
        )
    else:
        raw_pdf.patient = patient
        raw_pdf.examination = patient_examination
        raw_pdf.center = center
        raw_pdf.text = pdf_body
        raw_pdf.raw_meta = pdf_meta
        raw_pdf.anonym_examination_report = full_report
        if raw_pdf.pdf_hash != pdf_hash:
            # Avoid unique collision if a different row already owns the same content hash.
            collision = (
                RawPdfFile.objects.exclude(pk=raw_pdf.pk)
                .filter(pdf_hash=pdf_hash)
                .exists()
            )
            if collision:
                pdf_hash = hashlib.sha256(
                    pdf_bytes
                    + f"|report:{report_id}|v:{report_ref.version}".encode("utf-8")
                ).hexdigest()
            raw_pdf.pdf_hash = pdf_hash

    # New object may also collide with an existing row hash.
    if raw_pdf_is_new and RawPdfFile.objects.filter(pdf_hash=raw_pdf.pdf_hash).exists():
        raw_pdf.pdf_hash = hashlib.sha256(
            pdf_bytes + f"|report:{report_id}|v:{report_ref.version}".encode("utf-8")
        ).hexdigest()

    raw_pdf_ref = cast(_RawPdfFileLike, raw_pdf)
    raw_pdf_ref.file.save(pdf_filename, ContentFile[bytes](pdf_bytes), save=False)
    raw_pdf.save()

    return full_report.pk, raw_pdf.pk


def _update_patient_context(
    patient_examination: PatientExamination, patient_data: Mapping[str, object]
) -> None:
    patient = patient_examination.patient
    assert patient is not None, "PatientExamination must have an associated patient."
    patient_ref = cast(_PatientContextLike, patient)
    changed_fields: list[str] = []

    writable_field_map = {
        "patient_birth_date": "dob",
        "dob": "dob",
        "first_name": "first_name",
        "last_name": "last_name",
    }
    for payload_key, model_field in writable_field_map.items():
        if payload_key not in patient_data:
            continue
        value = patient_data[payload_key]
        if model_field == "dob":
            value = parse_report_date(value)
        if getattr(patient_ref, model_field) != value:
            setattr(patient_ref, model_field, value)
            changed_fields.append(model_field)

    if "patient_gender" in patient_data or "gender" in patient_data:
        gender_value = patient_data.get("patient_gender", patient_data.get("gender"))
        gender = _resolve_gender(gender_value)
        if gender_value not in (None, "") and gender is None:
            raise ValidationError({"patient_gender": "Unknown gender."})
        gender_id = cast(_IdentifiedLike, gender).id if gender is not None else None
        if patient_ref.gender_id != gender_id:
            patient_ref.gender = gender
            changed_fields.append("gender")

    if "center" in patient_data:
        center = _resolve_center(patient_data["center"])
        if patient_data["center"] not in (None, "") and center is None:
            raise ValidationError({"center": "Unknown center."})
        center_id = cast(_IdentifiedLike, center).id if center is not None else None
        if patient_ref.center_id != center_id:
            patient_ref.center = center
            changed_fields.append("center")

    if changed_fields:
        patient_ref.save(update_fields=sorted(set(changed_fields)))


def _sync_indications(
    patient_examination: PatientExamination,
    indications_payload: Sequence[Mapping[str, object]] | None,
) -> None:
    if indications_payload is None:
        return

    # Conservative skeleton: replace current indication rows if payload is provided.
    patient_examination.indications.all().delete()

    for item in indications_payload:
        examination_indication_id = item.get(
            "examination_indication_id", item.get("examination_indication")
        )
        indication_choice_id = item.get(
            "indication_choice_id", item.get("indication_choice")
        )
        if not examination_indication_id:
            continue
        PatientExaminationIndication.objects.create(
            patient_examination=patient_examination,
            examination_indication_id=examination_indication_id,
            indication_choice_id=indication_choice_id or None,
        )


def _resolve_submission_examination(
    patient_examination_id: int,
) -> PatientExamination:
    patient_examination = (
        PatientExamination.objects.select_related("patient", "examination")
        .filter(pk=patient_examination_id)
        .first()
    )
    if patient_examination is None:
        raise ValidationError(
            {"patient_examination_id": "PatientExamination not found."}
        )
    return patient_examination


def _resolve_submission_report(
    patient_examination: PatientExamination,
    *,
    report_id: int | None,
) -> tuple[PatientExaminationReport, bool]:
    if report_id is None:
        return (
            PatientExaminationReport(patient_examination=patient_examination),
            True,
        )
    report = (
        PatientExaminationReport.objects.select_for_update()
        .filter(pk=report_id, patient_examination=patient_examination)
        .first()
    )
    if report is None:
        raise ValidationError(
            {"report_id": "Report not found for patient examination."}
        )
    return report, False


def _validate_submission_version(
    report: _PatientExaminationReportLike,
    *,
    created: bool,
    expected_version: int | None,
) -> None:
    if expected_version is None or created or report.version == expected_version:
        return
    raise ValidationError(
        {
            "expected_version": (
                f"Version conflict. Current version is {report.version}, "
                f"expected {expected_version}."
            )
        }
    )


def _sync_submission_clinical_context(
    patient_examination: PatientExamination,
    *,
    patient_data: Mapping[str, object] | None,
    indications: Sequence[Mapping[str, object]] | None,
    findings: Sequence[Mapping[str, object]] | None,
    user: AuthUser | None,
) -> _FindingsSyncResult:
    if patient_data:
        _update_patient_context(patient_examination, patient_data)
    if indications is not None:
        _sync_indications(patient_examination, indications)
    if findings is None:
        return _FindingsSyncResult(
            warnings=[
                "No findings payload provided; normalized findings were not synced."
            ],
            persisted_record=None,
            persisted_record_updated_at=None,
        )
    sync_report_findings(patient_examination, findings, user=user)
    persisted_record = cast(
        dict[str, object],
        persist_patient_examination_dtypes_record_from_ledger(patient_examination),
    )
    return _FindingsSyncResult(
        warnings=[],
        persisted_record=persisted_record,
        persisted_record_updated_at=patient_examination.dtypes_record_updated_at,
    )


def _apply_submission_report_fields(
    report: _PatientExaminationReportLike,
    *,
    created: bool,
    user: AuthUser | None,
    template_name: str,
    template_version: str,
    template_hash: str,
    title: str,
    status: str,
    editor_payload: Mapping[str, object] | None,
    rendered_text: str,
    patient_data: Mapping[str, object] | None,
    history_context: Mapping[str, object],
) -> None:
    report.template_name = template_name
    report.template_version = _value_or_default(template_version, "")
    report.template_hash = _value_or_default(template_hash, "")
    report.title = _value_or_default(title, report.title)
    report.status = _value_or_default(
        status,
        PatientExaminationReport.Status.DRAFT.value,
    )
    report.editor_payload = report_json_safe_dict(_mapping_or_empty(editor_payload))
    report.rendered_text = _value_or_default(rendered_text, "")
    report.patient_context_snapshot = report_json_safe_dict(
        _mapping_or_empty(patient_data)
    )
    report.history_context_snapshot = report_json_safe_dict(history_context)
    report.updated_by = user
    if created:
        report.created_by = user
        report.version = 1
    else:
        report.version += 1


def _value_or_default(value: str, default: str) -> str:
    return value if value else default


def _mapping_or_empty(
    value: Mapping[str, object] | None,
) -> Mapping[str, object]:
    return value if value is not None else {}


def _apply_submission_finalization(
    report: _PatientExaminationReportLike,
    *,
    user: AuthUser | None,
) -> None:
    if report.status == PatientExaminationReport.Status.FINAL.value:
        report.finalized_at = timezone.now()
        report.finalized_by = user
        return
    report.finalized_at = None
    report.finalized_by = None


def _clear_finalized_submission_draft(
    patient_examination: PatientExamination,
    *,
    report_status: str,
) -> None:
    if report_status != PatientExaminationReport.Status.FINAL.value:
        return
    patient_examination.report_draft = {}
    patient_examination.draft_updated_at = None
    patient_examination.save(update_fields=["report_draft", "draft_updated_at"])


def _persist_final_report_artifacts(
    report: PatientExaminationReport,
    patient_examination: PatientExamination,
    *,
    report_status: str,
    rendered_text: str,
    warnings: list[str],
) -> tuple[int | None, int | None]:
    if report_status != PatientExaminationReport.Status.FINAL.value:
        return None, None
    try:
        return persist_report_pdf_artifact(
            report,
            patient_examination,
            rendered_text=rendered_text,
        )
    except Exception as error:
        warnings.append(
            "PDF artifact persistence failed "
            f"({type(error).__name__}). Report save continued."
        )
        return None, None


@transaction.atomic
def save_report_submission(
    *,
    patient_examination_id: int,
    template_name: str,
    editor_payload: Mapping[str, object] | None = None,
    rendered_text: str = "",
    status: str = PatientExaminationReport.Status.DRAFT.value,
    user: object | None = None,
    report_id: int | None = None,
    expected_version: int | None = None,
    patient_data: Mapping[str, object] | None = None,
    indications: Sequence[Mapping[str, object]] | None = None,
    findings: Sequence[Mapping[str, object]] | None = None,
    title: str = "",
    template_version: str = "",
    template_hash: str = "",
    history_limit: int = 5,
) -> SaveReportSubmissionResult:
    """
    Transactional persistence skeleton for edited report submissions.

    Persists:
    - report artifact (`PatientExaminationReport`)
    - patient demographics (allowlisted subset)
    - examination indications
    - normalized patient findings/classifications/interventions
    """
    if not template_name:
        raise ValidationError({"template_name": "template_name is required."})
    user_ref = cast(AuthUser | None, user)
    patient_examination = _resolve_submission_examination(patient_examination_id)
    report, created = _resolve_submission_report(
        patient_examination,
        report_id=report_id,
    )
    report_ref = cast(_PatientExaminationReportLike, report)
    _validate_submission_version(
        report_ref,
        created=created,
        expected_version=expected_version,
    )
    findings_result = _sync_submission_clinical_context(
        patient_examination,
        patient_data=patient_data,
        indications=indications,
        findings=findings,
        user=user_ref,
    )
    warnings = findings_result.warnings

    history_context = get_patient_examination_history_context(
        patient_examination, limit=history_limit
    )
    _apply_submission_report_fields(
        report_ref,
        created=created,
        user=user_ref,
        template_name=template_name,
        template_version=template_version,
        template_hash=template_hash,
        title=title,
        status=status,
        editor_payload=editor_payload,
        rendered_text=rendered_text,
        patient_data=patient_data,
        history_context=history_context,
    )
    _apply_submission_finalization(report_ref, user=user_ref)
    report_ref.save()
    _clear_finalized_submission_draft(
        patient_examination,
        report_status=report_ref.status,
    )
    persisted_report_artifact_id, persisted_pdf_artifact_id = (
        _persist_final_report_artifacts(
            report,
            patient_examination,
            report_status=report_ref.status,
            rendered_text=report_ref.rendered_text,
            warnings=warnings,
        )
    )

    return SaveReportSubmissionResult(
        report=report,
        created=created,
        warnings=warnings,
        history_context=cast(dict[str, object], history_context),
        persisted_dtypes_record=findings_result.persisted_record,
        persisted_dtypes_record_updated_at=(
            findings_result.persisted_record_updated_at
        ),
        persisted_report_artifact_id=persisted_report_artifact_id,
        persisted_pdf_artifact_id=persisted_pdf_artifact_id,
    )
