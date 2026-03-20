from __future__ import annotations

import hashlib
import re
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from endoreg_db.models import (
    AnonymExaminationReport,
    Center,
    Finding,
    FindingClassification,
    FindingClassificationChoice,
    FindingIntervention,
    Gender,
    PatientExamination,
    PatientExaminationIndication,
    PatientExaminationReport,
    PatientFinding,
    PatientFindingClassification,
    PatientFindingIntervention,
    RawPdfFile,
)
from endoreg_db.services.report_history import get_patient_examination_history_context

User = get_user_model()


@dataclass(slots=True)
class SaveReportSubmissionResult:
    report: PatientExaminationReport
    created: bool
    warnings: list[str]
    history_context: dict[str, Any]
    requirement_guidance: dict[str, Any]
    persisted_report_artifact_id: int | None = None
    persisted_pdf_artifact_id: int | None = None


def _resolve_gender(value: Any) -> Gender | None:
    if value in (None, ""):
        return None
    if isinstance(value, int):
        return Gender.objects.filter(pk=value).first()
    if isinstance(value, str):
        return Gender.objects.filter(name=value).first()
    return None


def _resolve_center(value: Any) -> Center | None:
    if value in (None, ""):
        return None
    if isinstance(value, int):
        return Center.objects.filter(pk=value).first()
    if isinstance(value, str):
        return Center.objects.filter(name=value).first()
    return None


def _resolve_finding(value: Any) -> Finding | None:
    if value in (None, ""):
        return None
    if isinstance(value, int):
        return Finding.objects.filter(pk=value).first()
    if isinstance(value, str):
        return Finding.objects.filter(name=value).first()
    return None


def _resolve_finding_classification(value: Any) -> FindingClassification | None:
    if value in (None, ""):
        return None
    if isinstance(value, int):
        return FindingClassification.objects.filter(pk=value).first()
    if isinstance(value, str):
        return FindingClassification.objects.filter(name=value).first()
    return None


def _resolve_finding_classification_choice(
    value: Any,
) -> FindingClassificationChoice | None:
    if value in (None, ""):
        return None
    if isinstance(value, int):
        return FindingClassificationChoice.objects.filter(pk=value).first()
    if isinstance(value, str):
        return FindingClassificationChoice.objects.filter(name=value).first()
    return None


def _resolve_finding_intervention(value: Any) -> FindingIntervention | None:
    if value in (None, ""):
        return None
    if isinstance(value, int):
        return FindingIntervention.objects.filter(pk=value).first()
    if isinstance(value, str):
        return FindingIntervention.objects.filter(name=value).first()
    return None


def _parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise ValidationError({"date": "Invalid date format; expected YYYY-MM-DD."})


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    raise ValidationError(
        {"datetime": "Invalid datetime format; expected ISO-8601 datetime."}
    )


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
) -> tuple[int | None, int | None]:
    """
    Create/update linked full-report + pdf media artifacts for a persisted report.

    Returns:
        (anonym_examination_report_id, raw_pdf_file_id)
    """
    patient = patient_examination.patient
    center = getattr(patient, "center", None)
    report_date = patient_examination.date_start

    report_title = report.title or f"{report.template_name} report"
    pdf_body = rendered_text or report.rendered_text or ""
    pdf_meta = {
        "source": "patient_examination_report",
        "patient_examination_report_id": report.id,
        "template_name": report.template_name,
        "template_version": report.template_version,
        "template_hash": report.template_hash,
        "version": report.version,
        "status": report.status,
        "generated_at": timezone.now().isoformat(),
    }
    if report.editor_payload:
        pdf_meta["editor_payload"] = report.editor_payload

    pdf_bytes: bytes
    try:
        from endoreg_db.services.report_pdf_renderer import (
            build_report_template_pdf_payload,
            render_pdf_with_rust_renderer,
        )

        payload = build_report_template_pdf_payload(
            report=report,
            patient_examination=patient_examination,
            section_blocks=None,
            frame_image_paths=None,
        )
        with tempfile.TemporaryDirectory(prefix="endoreg_report_pdf_") as tmp_dir:
            out_path = Path(tmp_dir) / "report.pdf"
            render_pdf_with_rust_renderer(payload, output_path=out_path)
            pdf_bytes = out_path.read_bytes()
    except Exception:
        pdf_bytes = _render_minimal_pdf_bytes(
            title=report_title,
            body_text=pdf_body,
        )
    pdf_hash = hashlib.sha256(pdf_bytes).hexdigest()

    full_report = AnonymExaminationReport.objects.filter(
        meta__patient_examination_report_id=report.id
    ).first()
    if full_report is None:
        full_report = AnonymExaminationReport(
            patient_examination=patient_examination,
            patient=patient,
            center=center,
        )
    full_report.patient = patient
    full_report.center = center
    full_report.text = pdf_body
    full_report.meta = pdf_meta
    full_report.date = report_date
    full_report.time = None

    pdf_filename = _safe_file_component(
        f"report_{patient_examination.id}_r{report.id}_v{report.version}.pdf",
        fallback=f"report_{report.id}.pdf",
    )

    # Keep full report file in sync too (optional but useful for timeline/display)
    full_report.file.save(pdf_filename, ContentFile(pdf_bytes), save=False)
    full_report.save()

    raw_pdf = RawPdfFile.objects.filter(anonym_examination_report=full_report).first()
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
                    + f"|report:{report.id}|v:{report.version}".encode("utf-8")
                ).hexdigest()
            raw_pdf.pdf_hash = pdf_hash

    # New object may also collide with an existing row hash.
    if (
        raw_pdf.pk is None
        and RawPdfFile.objects.filter(pdf_hash=raw_pdf.pdf_hash).exists()
    ):
        raw_pdf.pdf_hash = hashlib.sha256(
            pdf_bytes + f"|report:{report.id}|v:{report.version}".encode("utf-8")
        ).hexdigest()

    raw_pdf.file.save(pdf_filename, ContentFile(pdf_bytes), save=False)
    raw_pdf.save()

    return full_report.pk, raw_pdf.pk


def _update_patient_context(
    patient_examination: PatientExamination, patient_data: dict[str, Any]
) -> None:
    patient = patient_examination.patient
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
            value = _parse_date(value)
        if getattr(patient, model_field) != value:
            setattr(patient, model_field, value)
            changed_fields.append(model_field)

    if "patient_gender" in patient_data or "gender" in patient_data:
        gender_value = patient_data.get("patient_gender", patient_data.get("gender"))
        gender = _resolve_gender(gender_value)
        if gender_value not in (None, "") and gender is None:
            raise ValidationError({"patient_gender": "Unknown gender."})
        if patient.gender_id != (gender.id if gender else None):
            patient.gender = gender
            changed_fields.append("gender")

    if "center" in patient_data:
        center = _resolve_center(patient_data["center"])
        if patient_data["center"] not in (None, "") and center is None:
            raise ValidationError({"center": "Unknown center."})
        if patient.center_id != (center.id if center else None):
            patient.center = center
            changed_fields.append("center")

    if changed_fields:
        patient.save(update_fields=sorted(set(changed_fields)))


def _sync_indications(
    patient_examination: PatientExamination,
    indications_payload: list[dict[str, Any]],
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


def _sync_patient_finding_classifications(
    patient_finding: PatientFinding,
    classifications_payload: list[dict[str, Any]],
) -> None:
    existing_active = list(patient_finding.classifications.filter(is_active=True))
    matched_ids: set[int] = set()

    for item in classifications_payload:
        classification = _resolve_finding_classification(
            item.get("classification_id", item.get("classification"))
        )
        classification_choice = _resolve_finding_classification_choice(
            item.get("classification_choice_id", item.get("classification_choice"))
        )
        if classification is None or classification_choice is None:
            raise ValidationError(
                {"classifications": "Unknown classification or classification choice."}
            )

        match = next(
            (
                row
                for row in existing_active
                if row.classification_id == classification.id
                and row.classification_choice_id == classification_choice.id
            ),
            None,
        )
        if match is None:
            match = PatientFindingClassification.objects.create(
                finding=patient_finding,
                classification=classification,
                classification_choice=classification_choice,
                subcategories=item.get("subcategories"),
                numerical_descriptors=item.get("numerical_descriptors"),
            )
        else:
            changed = False
            if "subcategories" in item and match.subcategories != item.get(
                "subcategories"
            ):
                match.subcategories = item.get("subcategories")
                changed = True
            if (
                "numerical_descriptors" in item
                and match.numerical_descriptors != item.get("numerical_descriptors")
            ):
                match.numerical_descriptors = item.get("numerical_descriptors")
                changed = True
            if not match.is_active:
                match.is_active = True
                changed = True
            if changed:
                match.save()
        matched_ids.add(match.id)

    for row in existing_active:
        if row.id not in matched_ids and row.is_active:
            row.is_active = False
            row.save(update_fields=["is_active"])


def _sync_patient_finding_interventions(
    patient_finding: PatientFinding,
    interventions_payload: list[dict[str, Any]],
) -> None:
    existing_active = list(patient_finding.interventions.filter(is_active=True))
    matched_ids: set[int] = set()

    for item in interventions_payload:
        intervention = _resolve_finding_intervention(
            item.get("intervention_id", item.get("intervention"))
        )
        if intervention is None:
            raise ValidationError({"interventions": "Unknown intervention."})

        state = item.get("state")
        item_date = _parse_date(item.get("date")) if "date" in item else None
        time_start = (
            _parse_datetime(item.get("time_start")) if "time_start" in item else None
        )
        time_end = _parse_datetime(item.get("time_end")) if "time_end" in item else None

        match = next(
            (
                row
                for row in existing_active
                if row.intervention_id == intervention.id and row.state == state
            ),
            None,
        )
        if match is None:
            match = PatientFindingIntervention.objects.create(
                finding=patient_finding,
                intervention=intervention,
                state=state,
                date=item_date,
                time_start=time_start,
                time_end=time_end,
                is_active=True,
            )
        else:
            changed = False
            if match.date != item_date:
                match.date = item_date
                changed = True
            if match.time_start != time_start:
                match.time_start = time_start
                changed = True
            if match.time_end != time_end:
                match.time_end = time_end
                changed = True
            if not match.is_active:
                match.is_active = True
                changed = True
            if changed:
                match.save()
        matched_ids.add(match.id)

    for row in existing_active:
        if row.id not in matched_ids and row.is_active:
            row.is_active = False
            row.save(update_fields=["is_active"])


def _sync_findings(
    patient_examination: PatientExamination,
    findings_payload: list[dict[str, Any]],
    *,
    user: Any | None,
) -> None:
    existing_active = list(
        patient_examination.patient_findings.filter(is_active=True).select_related(
            "finding"
        )
    )
    matched_ids: set[int] = set()

    for item in findings_payload:
        finding = _resolve_finding(item.get("finding_id", item.get("finding")))
        if finding is None:
            raise ValidationError({"findings": "Unknown finding."})

        match = next(
            (pf for pf in existing_active if pf.finding_id == finding.id), None
        )
        if match is None:
            match = PatientFinding(
                patient_examination=patient_examination,
                finding=finding,
                created_by=user,
                updated_by=user,
                is_active=True,
            )
            match.save()
        else:
            changed_fields: list[str] = []
            if not match.is_active:
                match.is_active = True
                changed_fields.append("is_active")
            if match.updated_by_id != (user.id if user else None):
                match.updated_by = user
                changed_fields.append("updated_by")
            if changed_fields:
                match.save(update_fields=changed_fields)
        matched_ids.add(match.id)

        _sync_patient_finding_classifications(match, item.get("classifications", []))
        _sync_patient_finding_interventions(match, item.get("interventions", []))

    for row in existing_active:
        if row.id in matched_ids:
            continue
        row.is_active = False
        row.deactivated_at = timezone.now()
        row.deactivated_by = user
        row.updated_by = user
        row.save(
            update_fields=[
                "is_active",
                "deactivated_at",
                "deactivated_by",
                "updated_by",
            ]
        )


@transaction.atomic
def save_report_submission(
    *,
    patient_examination_id: int,
    template_name: str,
    editor_payload: dict[str, Any] | None = None,
    rendered_text: str = "",
    status: str = PatientExaminationReport.Status.DRAFT,
    user: Any | None = None,
    report_id: int | None = None,
    expected_version: int | None = None,
    patient_data: dict[str, Any] | None = None,
    indications: list[dict[str, Any]] | None = None,
    findings: list[dict[str, Any]] | None = None,
    title: str = "",
    template_version: str = "",
    template_hash: str = "",
    history_limit: int = 5,
    selected_requirement_set_ids: list[int] | None = None,
    evaluate_requirements: bool = True,
) -> SaveReportSubmissionResult:
    """
    Transactional persistence skeleton for edited report submissions.

    Persists:
    - report artifact (`PatientExaminationReport`)
    - patient demographics (allowlisted subset)
    - examination indications
    - normalized patient findings/classifications/interventions
    """
    warnings: list[str] = []
    patient_examination = (
        PatientExamination.objects.select_related("patient", "examination")
        .filter(pk=patient_examination_id)
        .first()
    )
    if patient_examination is None:
        raise ValidationError(
            {"patient_examination_id": "PatientExamination not found."}
        )

    if not template_name:
        raise ValidationError({"template_name": "template_name is required."})

    if report_id is not None:
        report = (
            PatientExaminationReport.objects.select_for_update()
            .filter(pk=report_id, patient_examination=patient_examination)
            .first()
        )
        if report is None:
            raise ValidationError(
                {"report_id": "Report not found for patient examination."}
            )
        created = False
    else:
        report = PatientExaminationReport(patient_examination=patient_examination)
        created = True

    if (
        expected_version is not None
        and not created
        and report.version != expected_version
    ):
        raise ValidationError(
            {
                "expected_version": (
                    f"Version conflict. Current version is {report.version}, "
                    f"expected {expected_version}."
                )
            }
        )

    if patient_data:
        _update_patient_context(patient_examination, patient_data)

    if indications is not None:
        _sync_indications(patient_examination, indications)

    if findings is not None:
        _sync_findings(patient_examination, findings, user=user)
    else:
        warnings.append(
            "No findings payload provided; normalized findings were not synced."
        )

    history_context = get_patient_examination_history_context(
        patient_examination, limit=history_limit
    )
    requirement_guidance: dict[str, Any] = {}

    requested_status = status or PatientExaminationReport.Status.DRAFT

    if evaluate_requirements:
        try:
            from endoreg_db.services.lookup_service import (
                evaluate_patient_exam_requirement_guidance,
                load_patient_exam_for_eval,
            )

            pe_for_eval = load_patient_exam_for_eval(patient_examination.id)
            requirement_guidance = evaluate_patient_exam_requirement_guidance(
                pe_for_eval,
                selected_requirement_set_ids=selected_requirement_set_ids,
            )

            failed_req_ids = [
                req_id
                for req_id, ok in (
                    requirement_guidance.get("requirement_status", {}) or {}
                ).items()
                if ok is False
            ]
            failed_set_ids = [
                rs_id
                for rs_id, ok in (
                    requirement_guidance.get("requirement_set_status", {}) or {}
                ).items()
                if ok is False
            ]
            if failed_req_ids:
                warnings.append(
                    f"Requirement guidance: {len(failed_req_ids)} requirement(s) are currently unmet."
                )
            if (
                requested_status == PatientExaminationReport.Status.FINAL
                and failed_set_ids
            ):
                warnings.append(
                    "Final report saved with guideline deviations. "
                    "This is advisory-only and does not block clinician workflow."
                )
        except Exception as exc:
            warnings.append(
                f"Requirement guidance unavailable ({type(exc).__name__}). Report save continued."
            )

    report.template_name = template_name
    report.template_version = template_version or ""
    report.template_hash = template_hash or ""
    report.title = title or report.title
    report.status = requested_status
    report.editor_payload = editor_payload or {}
    report.rendered_text = rendered_text or ""
    report.patient_context_snapshot = patient_data or {}
    report.history_context_snapshot = history_context
    report.updated_by = user
    if created:
        report.created_by = user
        report.version = 1
    else:
        report.version += 1

    if report.status == PatientExaminationReport.Status.FINAL:
        report.finalized_at = timezone.now()
        report.finalized_by = user
    else:
        report.finalized_at = None
        report.finalized_by = None

    report.save()

    if report.status == PatientExaminationReport.Status.FINAL:
        patient_examination.report_draft = {}
        patient_examination.draft_updated_at = None
        patient_examination.save(update_fields=["report_draft", "draft_updated_at"])

    persisted_report_artifact_id: int | None = None
    persisted_pdf_artifact_id: int | None = None
    if report.status == PatientExaminationReport.Status.FINAL:
        try:
            (
                persisted_report_artifact_id,
                persisted_pdf_artifact_id,
            ) = persist_report_pdf_artifact(
                report,
                patient_examination,
                rendered_text=report.rendered_text,
            )
        except Exception as exc:
            warnings.append(
                f"PDF artifact persistence failed ({type(exc).__name__}). Report save continued."
            )

    return SaveReportSubmissionResult(
        report=report,
        created=created,
        warnings=warnings,
        history_context=history_context,
        requirement_guidance=requirement_guidance,
        persisted_report_artifact_id=persisted_report_artifact_id,
        persisted_pdf_artifact_id=persisted_pdf_artifact_id,
    )
