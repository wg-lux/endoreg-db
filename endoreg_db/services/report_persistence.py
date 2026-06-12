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
from endoreg_db.models.medical.finding.finding import Finding
from endoreg_db.models.medical.finding.finding_classification import (
    FindingClassification,
    FindingClassificationChoice,
)
from endoreg_db.models.medical.finding.finding_intervention import FindingIntervention
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.medical.patient.patient_examination_indication import (
    PatientExaminationIndication,
)
from endoreg_db.models.medical.patient.patient_finding import PatientFinding
from endoreg_db.models.medical.patient.patient_finding_classification import (
    PatientFindingClassification,
)
from endoreg_db.models.medical.patient.patient_finding_intervention import (
    PatientFindingIntervention,
)
from endoreg_db.models.other.gender import Gender
from endoreg_db.models.report.patient_examination_report import PatientExaminationReport
from endoreg_db.schemas import validate_raw_pdf_meta_payload
from endoreg_db.services.dtypes_records import (
    persist_patient_examination_dtypes_record_from_ledger,
)
from endoreg_db.services.report_history import get_patient_examination_history_context
from lx_dtypes.models.contracts.patient_examination_report import (
    PatientFindingClassificationSyncData,
    PatientFindingInterventionSyncData,
    report_json_safe_dict,
)
from lx_dtypes.models.contracts.patient_finding_classification_runtime import (
    PatientFindingClassificationNumericalDescriptorsData,
    PatientFindingClassificationNumericalDescriptorsPayload,
    PatientFindingClassificationSubcategoriesData,
    PatientFindingClassificationSubcategoriesPayload,
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


class _PatientFindingClassificationLike(Protocol):
    classification_id: int
    classification_choice_id: int
    is_active: bool
    subcategories: PatientFindingClassificationSubcategoriesData | None
    numerical_descriptors: PatientFindingClassificationNumericalDescriptorsData | None

    def save(self, *args: object, **kwargs: object) -> None: ...


class _PatientFindingInterventionLike(Protocol):
    intervention_id: int
    state: str | None
    date: date | None
    time_start: datetime | None
    time_end: datetime | None
    is_active: bool

    def save(self, *args: object, **kwargs: object) -> None: ...


class _PatientFindingLike(Protocol):
    finding_id: int
    updated_by_id: int | None
    is_active: bool
    deactivated_at: datetime | None
    deactivated_by: AuthUser | None
    updated_by: AuthUser | None

    def save(self, *args: object, **kwargs: object) -> None: ...



class _PatientFindingClassificationManager(Protocol):
    def filter(self, **kwargs: object) -> Sequence[PatientFindingClassification]: ...


class _PatientFindingInterventionManager(Protocol):
    def filter(self, **kwargs: object) -> Sequence[PatientFindingIntervention]: ...


class _PatientFindingReverseRelations(Protocol):
    classifications: _PatientFindingClassificationManager
    interventions: _PatientFindingInterventionManager


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


def _resolve_finding(value: object) -> Finding | None:
    if value in (None, ""):
        return None
    if isinstance(value, int):
        return Finding.objects.filter(pk=value).first()
    if isinstance(value, str):
        return Finding.objects.filter(name=value).first()
    return None


def _resolve_finding_classification(value: object) -> FindingClassification | None:
    if value in (None, ""):
        return None
    if isinstance(value, int):
        return FindingClassification.objects.filter(pk=value).first()
    if isinstance(value, str):
        return FindingClassification.objects.filter(name=value).first()
    return None


def _resolve_finding_classification_choice(
    value: object,
) -> FindingClassificationChoice | None:
    if value in (None, ""):
        return None
    if isinstance(value, int):
        return FindingClassificationChoice.objects.filter(pk=value).first()
    if isinstance(value, str):
        return FindingClassificationChoice.objects.filter(name=value).first()
    return None


def _resolve_finding_intervention(value: object) -> FindingIntervention | None:
    if value in (None, ""):
        return None
    if isinstance(value, int):
        return FindingIntervention.objects.filter(pk=value).first()
    if isinstance(value, str):
        return FindingIntervention.objects.filter(name=value).first()
    return None


def _parse_date(value: object) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise ValidationError({"date": "Invalid date format; expected YYYY-MM-DD."})


def _parse_datetime(value: object) -> datetime | None:
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


def _normalize_pdf_meta_payload(
    value: dict[str, object],
) -> ReportMetaJsonObject:
    validated_pdf_meta = validate_raw_pdf_meta_payload(value)
    return cast(ReportMetaJsonObject, validated_pdf_meta or {})


def _normalize_patient_finding_classification_subcategories(
    value: object | None,
) -> PatientFindingClassificationSubcategoriesData | None:
    if value is None:
        return None
    return cast(
        PatientFindingClassificationSubcategoriesData,
        PatientFindingClassificationSubcategoriesPayload.model_validate(
            value
        ).model_dump(mode="python"),
    )


def _normalize_patient_finding_classification_numerical_descriptors(
    value: object | None,
) -> PatientFindingClassificationNumericalDescriptorsData | None:
    if value is None:
        return None
    return cast(
        PatientFindingClassificationNumericalDescriptorsData,
        PatientFindingClassificationNumericalDescriptorsPayload.model_validate(
            value
        ).model_dump(mode="python"),
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
    full_report_ref.file.save(pdf_filename, ContentFile(pdf_bytes), save=False)
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
    raw_pdf_ref.file.save(pdf_filename, ContentFile(pdf_bytes), save=False)
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
            value = _parse_date(value)
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


def _sync_patient_finding_classifications(
    patient_finding: PatientFinding,
    classifications_payload: Sequence[PatientFindingClassificationSyncData],
) -> None:
    patient_finding_relations = cast(_PatientFindingReverseRelations, patient_finding)
    existing_active: list[PatientFindingClassification] = list(
        patient_finding_relations.classifications.filter(is_active=True)
    )
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
        classification_id = cast(_IdentifiedLike, classification).id
        classification_choice_id = cast(_IdentifiedLike, classification_choice).id

        match = next(
            (
                cast(_PatientFindingClassificationLike, row)
                for row in existing_active
                if cast(_PatientFindingClassificationLike, row).classification_id
                == classification_id
                and cast(
                    _PatientFindingClassificationLike, row
                ).classification_choice_id
                == classification_choice_id
            ),
            None,
        )
        if match is None:
            create_kwargs: dict[str, object] = {
                "finding": patient_finding,
                "classification": classification,
                "classification_choice": classification_choice,
            }
            subcategories = _normalize_patient_finding_classification_subcategories(
                item.get("subcategories") if "subcategories" in item else None
            )
            if subcategories is not None:
                create_kwargs["subcategories"] = subcategories
            numerical_descriptors = (
                _normalize_patient_finding_classification_numerical_descriptors(
                    item.get("numerical_descriptors")
                    if "numerical_descriptors" in item
                    else None
                )
            )
            if numerical_descriptors is not None:
                create_kwargs["numerical_descriptors"] = numerical_descriptors
            match = PatientFindingClassification.objects.create(**create_kwargs)
        else:
            changed = False
            subcategories = _normalize_patient_finding_classification_subcategories(
                item.get("subcategories") if "subcategories" in item else None
            )
            if subcategories is not None and match.subcategories != subcategories:
                match.subcategories = subcategories
                changed = True
            numerical_descriptors = (
                _normalize_patient_finding_classification_numerical_descriptors(
                    item.get("numerical_descriptors")
                    if "numerical_descriptors" in item
                    else None
                )
            )
            if (
                numerical_descriptors is not None
                and match.numerical_descriptors != numerical_descriptors
            ):
                match.numerical_descriptors = numerical_descriptors
                changed = True
            if not match.is_active:
                match.is_active = True
                changed = True
            if changed:
                match.save()
        matched_ids.add(cast(_IdentifiedLike, match).id)

    for row in existing_active:
        row_ref = cast(_PatientFindingClassificationLike, row)
        if cast(_IdentifiedLike, row).id not in matched_ids and row_ref.is_active:
            row_ref.is_active = False
            row_ref.save(update_fields=["is_active"])


def _sync_patient_finding_interventions(
    patient_finding: PatientFinding,
    interventions_payload: Sequence[PatientFindingInterventionSyncData],
) -> None:
    patient_finding_relations = cast(_PatientFindingReverseRelations, patient_finding)
    existing_active: list[PatientFindingIntervention] = list(
        patient_finding_relations.interventions.filter(is_active=True)
    )
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
                cast(_PatientFindingInterventionLike, row)
                for row in existing_active
                if cast(_PatientFindingInterventionLike, row).intervention_id
                == cast(_IdentifiedLike, intervention).id
                and cast(_PatientFindingInterventionLike, row).state == state
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
        matched_ids.add(cast(_IdentifiedLike, match).id)

    for row in existing_active:
        row_ref = cast(_PatientFindingInterventionLike, row)
        if cast(_IdentifiedLike, row).id not in matched_ids and row_ref.is_active:
            row_ref.is_active = False
            row_ref.save(update_fields=["is_active"])


def _sync_findings(
    patient_examination: PatientExamination,
    findings_payload: Sequence[Mapping[str, object]],
    *,
    user: AuthUser | None,
) -> None:
    user_ref = user
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
            (
                cast(_PatientFindingLike, pf)
                for pf in existing_active
                if cast(_PatientFindingLike, pf).finding_id
                == cast(_IdentifiedLike, finding).id
            ),
            None,
        )
        if match is None:
            match = PatientFinding(
                patient_examination=patient_examination,
                finding=finding,
                created_by=user_ref,
                updated_by=user_ref,
                is_active=True,
            )
            match.save()
        else:
            changed_fields: list[str] = []
            match_ref = match
            if not match_ref.is_active:
                match_ref.is_active = True
                changed_fields.append("is_active")
            user_id = (
                cast(_IdentifiedLike, user_ref).id if user_ref is not None else None
            )
            if match_ref.updated_by_id != user_id:
                match_ref.updated_by = user_ref
                changed_fields.append("updated_by")
            if changed_fields:
                match.save(update_fields=changed_fields)
        matched_ids.add(cast(_IdentifiedLike, match).id)

        match_model = cast(PatientFinding, match)
        _sync_patient_finding_classifications(
            match_model,
            cast(
                Sequence[PatientFindingClassificationSyncData],
                item.get("classifications", []),
            ),
        )
        _sync_patient_finding_interventions(
            match_model,
            cast(
                Sequence[PatientFindingInterventionSyncData],
                item.get("interventions", []),
            ),
        )

    for row in existing_active:
        if cast(_IdentifiedLike, row).id in matched_ids:
            continue
        row_ref = cast(_PatientFindingLike, row)
        row_ref.is_active = False
        row_ref.deactivated_at = timezone.now()
        row_ref.deactivated_by = user_ref
        row_ref.updated_by = user_ref
        row_ref.save(
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
    user_ref = cast(AuthUser | None, user)
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
    report_ref = cast(_PatientExaminationReportLike, report)

    if (
        expected_version is not None
        and not created
        and report_ref.version != expected_version
    ):
        raise ValidationError(
            {
                "expected_version": (
                    f"Version conflict. Current version is {report_ref.version}, "
                    f"expected {expected_version}."
                )
            }
        )

    if patient_data:
        _update_patient_context(patient_examination, patient_data)

    if indications is not None:
        _sync_indications(patient_examination, indications)

    persisted_dtypes_record: dict[str, object] | None = None
    persisted_dtypes_record_updated_at: datetime | None = None
    if findings is not None:
        _sync_findings(patient_examination, findings, user=user_ref)
        persisted_dtypes_record = cast(
            dict[str, object],
            persist_patient_examination_dtypes_record_from_ledger(patient_examination),
        )
        persisted_dtypes_record_updated_at = (
            patient_examination.dtypes_record_updated_at
        )
    else:
        warnings.append(
            "No findings payload provided; normalized findings were not synced."
        )

    history_context = get_patient_examination_history_context(
        patient_examination, limit=history_limit
    )

    requested_status = status or PatientExaminationReport.Status.DRAFT.value

    report_ref.template_name = template_name
    report_ref.template_version = template_version or ""
    report_ref.template_hash = template_hash or ""
    report_ref.title = title or report_ref.title
    report_ref.status = requested_status
    report_ref.editor_payload = report_json_safe_dict(editor_payload or {})
    report_ref.rendered_text = rendered_text or ""
    report_ref.patient_context_snapshot = report_json_safe_dict(patient_data or {})
    report_ref.history_context_snapshot = report_json_safe_dict(history_context)
    report_ref.updated_by = user_ref
    if created:
        report_ref.created_by = user_ref
        report_ref.version = 1
    else:
        report_ref.version += 1

    if report_ref.status == PatientExaminationReport.Status.FINAL.value:
        report_ref.finalized_at = timezone.now()
        report_ref.finalized_by = user_ref
    else:
        report_ref.finalized_at = None
        report_ref.finalized_by = None

    report_ref.save()

    if report_ref.status == PatientExaminationReport.Status.FINAL.value:
        patient_examination.report_draft = {}
        patient_examination.draft_updated_at = None
        patient_examination.save(update_fields=["report_draft", "draft_updated_at"])

    persisted_report_artifact_id: int | None = None
    persisted_pdf_artifact_id: int | None = None
    if report_ref.status == PatientExaminationReport.Status.FINAL.value:
        try:
            (
                persisted_report_artifact_id,
                persisted_pdf_artifact_id,
            ) = persist_report_pdf_artifact(
                report,
                patient_examination,
                rendered_text=report_ref.rendered_text,
            )
        except Exception as exc:
            warnings.append(
                f"PDF artifact persistence failed ({type(exc).__name__}). Report save continued."
            )

    return SaveReportSubmissionResult(
        report=report,
        created=created,
        warnings=warnings,
        history_context=cast(dict[str, object], history_context),
        persisted_dtypes_record=persisted_dtypes_record,
        persisted_dtypes_record_updated_at=persisted_dtypes_record_updated_at,
        persisted_report_artifact_id=persisted_report_artifact_id,
        persisted_pdf_artifact_id=persisted_pdf_artifact_id,
    )
