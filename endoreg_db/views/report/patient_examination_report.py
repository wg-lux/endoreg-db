# pyright: reportPrivateUsage=false
from __future__ import annotations

import json
import random
from collections.abc import Mapping
from copy import deepcopy
from typing import Any, TypeVar, cast

from django.contrib.auth.models import User
from django.db import models, transaction
from django.db.models import Q, QuerySet
from django.http import HttpRequest
from django.utils import timezone
from lx_dtypes.models.contracts.patient_examination_report import (
    PatientExaminationReportMakeReportData,
    PatientExaminationReportMakeReportPayload,
    PatientExaminationReportSubmissionData,
    PatientExaminationReportSubmissionPayload,
    ReportExportFrameDetailData,
    ReportJsonObject,
    ReportPersistedArtifactsData,
    ReportPersistedArtifactsPayload,
    ReportSegmentFrameSelectionData,
    ReportSegmentFrameSelectionPayload,
    ReportSegmentSelectionMap,
    SegmentFramePreviewData,
    SegmentFrameSelectorItemData,
    SegmentFrameSelectorPatchPayload,
    SegmentFrameSelectorQueryPayload,
    SegmentFrameSelectorResponseData,
    dump_make_report_payload,
    dump_persisted_artifacts_payload,
    dump_report_submission_payload,
    dump_selector_patch_payload,
    dump_segment_frame_selection_payload,
    report_json_safe,
    report_json_safe_dict,
    validate_segment_selection_map,
)
from pydantic import ValidationError
from ninja import Query as NinjaQuery, Router
from ninja.errors import HttpError

from endoreg_db.helpers.model_ids import model_pk, optional_model_pk
from endoreg_db.models.label.label_video_segment.label_video_segment import (
    LabelVideoSegment,
)
from endoreg_db.models.media.frame.frame import Frame
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.pdf.report_file import AnonymExaminationReport
from endoreg_db.models.medical.finding.finding import Finding
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.medical.patient.patient_finding import PatientFinding
from endoreg_db.models.report.patient_examination_report import PatientExaminationReport
from endoreg_db.serializers.report.patient_examination_report import (
    PatientExaminationReportSchema,
    SegmentFrameSelectorResponseSchema,
)
from endoreg_db.services.report_history import get_patient_examination_history_context
from endoreg_db.services.report_persistence import (
    persist_report_pdf_artifact,
    save_report_submission,
)
from endoreg_db.utils.media_urls import (
    build_absolute_media_url,
    build_patient_timeline_path,
    build_pdf_stream_path,
    build_video_frame_stream_path,
)

router = Router(tags=["patient-examination-reports"])

ModelT = TypeVar("ModelT", bound=models.Model)


def _ninja_query(*args: Any, **kwargs: Any) -> Any:
    return cast(Any, NinjaQuery)(*args, **kwargs)


def _authenticated_user_from_request(request: object) -> User | None:
    user = cast(object | None, getattr(request, "user", None))
    if user is not None and bool(getattr(user, "is_authenticated", False)):
        return cast(User, user)
    return None


def _patient_examination_pk(patient_examination: PatientExamination) -> int:
    return model_pk(patient_examination)


def _segment_pk(segment: LabelVideoSegment) -> int:
    return model_pk(segment)


def _segment_video_id(segment: LabelVideoSegment) -> int:
    return int(cast(Any, segment).video_file_id)


def _segment_label_id(segment: LabelVideoSegment) -> int | None:
    label_id = getattr(segment, "label_id", None)
    return int(label_id) if isinstance(label_id, int) else None


def _segment_label_name(segment: LabelVideoSegment) -> str | None:
    label = cast(object | None, getattr(segment, "label", None))
    name = getattr(label, "name", None) if label is not None else None
    return str(name) if name is not None else None


def _segment_start(segment: LabelVideoSegment) -> int:
    return int(cast(Any, segment).start_frame_number)


def _segment_end(segment: LabelVideoSegment) -> int:
    return int(cast(Any, segment).end_frame_number)


def _segment_duration(segment: LabelVideoSegment) -> float | None:
    duration = getattr(segment, "segment_duration", None)
    return float(duration) if isinstance(duration, (int, float)) else None


def _frame_pk(frame: Frame) -> int:
    return model_pk(frame)


def _frame_number(frame: Frame) -> int:
    return int(cast(Any, frame).frame_number)


def _frame_timestamp(frame: Frame) -> float | None:
    timestamp = getattr(frame, "timestamp", None)
    return float(timestamp) if isinstance(timestamp, (int, float)) else None


def _frame_relative_path(frame: Frame) -> str:
    return str(cast(object, getattr(frame, "relative_path", "")))


def _patient_finding_pk(patient_finding: PatientFinding) -> int:
    return model_pk(patient_finding)


def _patient_finding_finding_id(patient_finding: PatientFinding) -> int | None:
    finding_id = getattr(patient_finding, "finding_id", None)
    return int(finding_id) if isinstance(finding_id, int) else None


def _patient_finding_finding_name(patient_finding: PatientFinding) -> str | None:
    finding = cast(object | None, getattr(patient_finding, "finding", None))
    name = getattr(finding, "name", None) if finding is not None else None
    return str(name) if name is not None else None


def _report_pk(report: PatientExaminationReport) -> int:
    return model_pk(report)


def _report_status(report: PatientExaminationReport) -> str:
    return str(cast(object, getattr(report, "status", "")))


def _report_template_name(report: PatientExaminationReport) -> str:
    return str(cast(object, getattr(report, "template_name", "")))


def _report_rendered_text(report: PatientExaminationReport) -> str:
    return str(cast(object, getattr(report, "rendered_text", "")))


def _report_patient_examination(
    report: PatientExaminationReport,
) -> PatientExamination:
    return cast(PatientExamination, getattr(report, "patient_examination"))


def _serialized_report_data(report: PatientExaminationReport) -> ReportJsonObject:
    # Ninja/Pydantic replacement for DRF serializer.data
    return report_json_safe_dict(
        PatientExaminationReportSchema.model_validate(report).model_dump(mode="json")
    )


def _assign_report_editor_payload(
    report: PatientExaminationReport,
    payload: ReportJsonObject,
) -> None:
    # Django JSONField stubs use JsonObject, while the report contract allows
    # JSON null. The payload is produced by report_json_safe_dict and is safe at
    # this persistence boundary.
    cast(Any, report).editor_payload = payload


def _assign_report_user_fields(
    report: PatientExaminationReport,
    *,
    finalized_by: User | None = None,
    updated_by: User | None = None,
) -> None:
    if finalized_by is not None or finalized_by is None:
        cast(Any, report).finalized_by = finalized_by
    if updated_by is not None or updated_by is None:
        cast(Any, report).updated_by = updated_by


def _assign_patient_finding_updated_by(
    patient_finding: PatientFinding,
    user: User,
) -> None:
    cast(Any, patient_finding).updated_by = user


def _request_json_object(request: HttpRequest) -> ReportJsonObject:
    try:
        decoded: object = json.loads(request.body.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise HttpError(400, "Request body must be UTF-8 encoded JSON.") from exc
    except json.JSONDecodeError as exc:
        raise HttpError(400, "Request body must be a valid JSON object.") from exc

    if isinstance(decoded, Mapping):
        decoded_mapping = cast(Mapping[object, object], decoded)
        mapped: dict[str, object] = {
            str(key): value for key, value in decoded_mapping.items()
        }
        if set(mapped) == {"payload"}:
            decoded = mapped["payload"]
    if not isinstance(decoded, Mapping):
        raise HttpError(400, "Request body must be a valid JSON object.")
    return report_json_safe_dict(cast(object, decoded))


class PatientExaminationReportApi:
    SEGMENT_FRAME_SELECTIONS_KEY = "report_segment_frame_selections"

    def __init__(self, request: HttpRequest) -> None:
        self.request = request

    def _is_privileged_user(self, user: object | None) -> bool:
        return bool(
            user
            and getattr(user, "is_authenticated", False)
            and (
                getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)
            )
        )

    def _allowed_center_ids_for_user(self, user: object | None) -> set[int] | None:
        from endoreg_db.services.center_access import resolve_allowed_center_ids

        allowed_center_ids = resolve_allowed_center_ids(user)
        return None if allowed_center_ids is None else set(allowed_center_ids)

    def _apply_center_scope(self, queryset: QuerySet[ModelT]) -> QuerySet[ModelT]:
        allowed_center_ids = self._allowed_center_ids_for_user(
            getattr(self.request, "user", None)
        )
        if allowed_center_ids is None:
            return queryset
        if not allowed_center_ids:
            return queryset.none()
        return queryset.filter(
            patient_examination__patient__center_id__in=allowed_center_ids
        )

    def _get_scoped_patient_examination(
        self,
        patient_examination_id: int,
    ) -> PatientExamination:
        queryset = self._apply_center_scope(
            PatientExamination.objects.select_related("patient", "examination")
        )
        patient_examination = queryset.filter(pk=patient_examination_id).first()
        if patient_examination is None:
            raise HttpError(
                403,
                "You do not have access to this patient examination or it does not exist.",
            )
        return patient_examination

    def get_queryset(
        self,
        *,
        patient_examination_id: int | None = None,
        status: str | None = None,
        template_name: str | None = None,
        is_active: bool | None = None,
        broad_list: bool = False,
    ) -> QuerySet[PatientExaminationReport]:
        queryset = self._apply_center_scope(
            PatientExaminationReport.objects.all().select_related(
                "patient_examination",
                "created_by",
                "updated_by",
                "finalized_by",
            )
        )

        if patient_examination_id is not None:
            queryset = queryset.filter(patient_examination_id=patient_examination_id)
        elif broad_list and not self._is_privileged_user(
            getattr(self.request, "user", None)
        ):
            queryset = queryset.none()

        if status is not None:
            queryset = queryset.filter(status=status)
        if template_name is not None:
            queryset = queryset.filter(template_name=template_name)
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active)

        return queryset.order_by("-updated_at", "-id")

    @staticmethod
    def _prediction_segment_query() -> Q:
        return Q(prediction_meta__isnull=False) | Q(source__name="prediction")

    def _build_persisted_artifacts_payload(
        self,
        *,
        patient_examination: PatientExamination,
        persisted_report_artifact_id: int | None,
        persisted_pdf_artifact_id: int | None,
    ) -> ReportPersistedArtifactsData | None:
        if not persisted_pdf_artifact_id and not persisted_report_artifact_id:
            return None

        patient_id = getattr(patient_examination, "patient_id", None)
        pdf_id = persisted_pdf_artifact_id

        payload = ReportPersistedArtifactsPayload(
            full_report_id=persisted_report_artifact_id,
            pdf_id=persisted_pdf_artifact_id,
            pdf_view_url=(
                build_absolute_media_url(
                    self.request,
                    build_pdf_stream_path(pdf_id, file_type="processed"),
                )
                if pdf_id is not None
                else None
            ),
            pdf_download_url=(
                build_absolute_media_url(
                    self.request,
                    build_pdf_stream_path(
                        pdf_id,
                        file_type="raw",
                        download=True,
                    ),
                )
                if pdf_id is not None
                else None
            ),
            patient_timeline_url=(
                build_absolute_media_url(
                    self.request,
                    build_patient_timeline_path(int(patient_id)),
                )
                if isinstance(patient_id, int)
                else None
            ),
        )
        return dump_persisted_artifacts_payload(payload)

    def _patient_examination_segments(
        self,
        patient_examination: PatientExamination,
    ) -> QuerySet[LabelVideoSegment]:
        return (
            LabelVideoSegment.objects.select_related("video_file", "label", "source")
            .prefetch_related("patient_findings__finding")
            .filter(self._patient_examination_segment_filter(patient_examination))
            .distinct()
            .order_by("video_file_id", "start_frame_number", "id")
        )

    def _patient_examination_segment_filter(
        self,
        patient_examination: PatientExamination,
    ) -> Q:
        segment_filter = Q(
            video_file__examination_id=_patient_examination_pk(patient_examination)
        )

        sensitive_meta_ids = self._patient_examination_sensitive_meta_ids(
            patient_examination
        )
        if sensitive_meta_ids:
            segment_filter |= Q(video_file__sensitive_meta_id__in=sensitive_meta_ids)

        return segment_filter

    @staticmethod
    def _patient_examination_sensitive_meta_ids(
        patient_examination: PatientExamination,
    ) -> set[int]:
        patient_examination_id = _patient_examination_pk(patient_examination)

        sensitive_meta_ids: set[int] = {
            int(sensitive_meta_id)
            for sensitive_meta_id in RawPdfFile.objects.filter(
                examination_id=patient_examination_id,
                sensitive_meta_id__isnull=False,
            ).values_list("sensitive_meta_id", flat=True)
        }

        sensitive_meta_ids.update(
            int(sensitive_meta_id)
            for sensitive_meta_id in AnonymExaminationReport.objects.filter(
                patient_examination_id=patient_examination_id,
                sensitive_meta_id__isnull=False,
            ).values_list("sensitive_meta_id", flat=True)
        )

        video = getattr(patient_examination, "video", None)
        sensitive_meta_id = getattr(video, "sensitive_meta_id", None)
        if isinstance(sensitive_meta_id, int):
            sensitive_meta_ids.add(sensitive_meta_id)

        return sensitive_meta_ids

    def _resolve_report_for_export(
        self,
        *,
        patient_examination: PatientExamination,
        report_id: int | None,
    ) -> PatientExaminationReport | None:
        queryset = self._apply_center_scope(
            PatientExaminationReport.objects.select_related(
                "patient_examination__patient"
            )
        ).filter(patient_examination=patient_examination)

        if report_id is not None:
            return queryset.filter(pk=report_id).first()

        return queryset.filter(is_active=True).order_by("-updated_at", "-id").first()

    @staticmethod
    def _segment_midpoint_frame(segment: LabelVideoSegment) -> int:
        return int((_segment_start(segment) + _segment_end(segment)) // 2)

    @staticmethod
    def _get_segment_selection_map(
        report: PatientExaminationReport,
    ) -> ReportSegmentSelectionMap:
        payload = report_json_safe_dict(getattr(report, "editor_payload", {}))
        selections = payload.get(
            PatientExaminationReportApi.SEGMENT_FRAME_SELECTIONS_KEY
        )
        return validate_segment_selection_map(selections)

    def _persist_segment_selection(
        self,
        *,
        report: PatientExaminationReport,
        segment_id: int,
        selection_value: ReportSegmentFrameSelectionData | None,
    ) -> None:
        # Important: your pasted DRF version builds payload but does not persist it.
        # This version locks, mutates, assigns editor_payload, and saves.
        with transaction.atomic():
            locked_report = PatientExaminationReport.objects.select_for_update().get(
                pk=report.pk
            )

            payload: ReportJsonObject = report_json_safe_dict(
                deepcopy(getattr(locked_report, "editor_payload", {}) or {})
            )
            selections = payload.get(self.SEGMENT_FRAME_SELECTIONS_KEY)
            selection_map = validate_segment_selection_map(selections)

            key = str(segment_id)
            if selection_value is None:
                selection_map.pop(key, None)
            else:
                selection_map[key] = selection_value

            payload[self.SEGMENT_FRAME_SELECTIONS_KEY] = report_json_safe(selection_map)

            _assign_report_editor_payload(locked_report, payload)
            _assign_report_user_fields(
                locked_report,
                updated_by=(
                    _authenticated_user_from_request(self.request)
                    or cast(Any, locked_report).updated_by
                ),
            )
            locked_report.save(
                update_fields=["editor_payload", "updated_by", "updated_at"]
            )

    def _selected_frame_for_export(
        self,
        *,
        segment: LabelVideoSegment,
        selection: ReportSegmentFrameSelectionData,
    ) -> Frame | None:
        stored_frame_number = selection.get("frame_number")
        if stored_frame_number:
            try:
                selected_frame_number = int(stored_frame_number)
            except (TypeError, ValueError):
                selected_frame_number = None
        else:
            selected_frame_number = None

        frame_qs = (
            segment.get_frames().filter(is_extracted=True).order_by("frame_number")
        )

        if selected_frame_number is not None:
            selected = frame_qs.filter(frame_number=selected_frame_number).first()
            if selected is not None:
                return selected

        midpoint = self._segment_midpoint_frame(segment)
        return frame_qs.filter(frame_number__gte=midpoint).first() or frame_qs.first()

    @staticmethod
    def _frame_caption(
        *,
        segment: LabelVideoSegment,
        frame: Frame,
        patient_finding: PatientFinding | None,
    ) -> str:
        parts = [
            _segment_label_name(segment) or "prediction",
            f"frame {_frame_number(frame)}",
        ]

        timestamp = _frame_timestamp(frame)
        if timestamp is not None:
            parts.append(f"{timestamp:.1f}s")

        finding_name = (
            _patient_finding_finding_name(patient_finding)
            if patient_finding is not None
            else None
        )
        if finding_name:
            parts.append(finding_name)

        return " | ".join(part for part in parts if part)

    def _collect_report_export_frames(
        self,
        *,
        patient_examination: PatientExamination,
        report: PatientExaminationReport,
        max_frames: int,
    ) -> tuple[list[str], list[str], list[ReportExportFrameDetailData], list[str]]:
        selection_map = self._get_segment_selection_map(report)

        selected_segment_ids: list[int] = []
        for key in selection_map:
            try:
                selected_segment_ids.append(int(key))
            except (TypeError, ValueError):
                continue

        segments = self._patient_examination_segments(patient_examination)
        if selected_segment_ids:
            segments = segments.filter(pk__in=selected_segment_ids)
        else:
            prediction_segments = segments.filter(self._prediction_segment_query())
            segments = prediction_segments if prediction_segments.exists() else segments

        frame_paths: list[str] = []
        captions: list[str] = []
        details: list[ReportExportFrameDetailData] = []
        warnings: list[str] = []

        for segment in segments[:max_frames]:
            segment_id = _segment_pk(segment)
            selection = selection_map.get(str(segment_id), {})

            frame = self._selected_frame_for_export(
                segment=segment,
                selection=selection,
            )
            if frame is None:
                warnings.append(f"No extracted frame found for segment {segment_id}.")
                continue

            frame_path = frame.file_path
            if not frame_path.is_file():
                warnings.append(f"Frame file missing for frame {_frame_pk(frame)}.")
                continue

            patient_finding = (
                segment.patient_findings.filter(
                    patient_examination=patient_examination,
                    is_active=True,
                )
                .select_related("finding")
                .order_by("-updated_at", "-id")
                .first()
            )

            caption = self._frame_caption(
                segment=segment,
                frame=frame,
                patient_finding=patient_finding,
            )

            video_id = _segment_video_id(segment)
            frame_number = _frame_number(frame)

            frame_paths.append(str(frame_path))
            captions.append(caption)
            details.append(
                {
                    "segment_id": segment_id,
                    "video_id": video_id,
                    "frame_id": _frame_pk(frame),
                    "frame_number": frame_number,
                    "label_name": _segment_label_name(segment),
                    "finding_name": (
                        _patient_finding_finding_name(patient_finding)
                        if patient_finding is not None
                        else None
                    ),
                    "stream_url": build_absolute_media_url(
                        self.request,
                        build_video_frame_stream_path(video_id, frame_number),
                    ),
                    "caption": caption,
                }
            )

        return frame_paths, captions, details, warnings

    @staticmethod
    def _build_report_export_blocks(
        *,
        report: PatientExaminationReport,
        frame_details: list[ReportExportFrameDetailData],
    ) -> list[dict[str, Any]]:
        rendered_text = _report_rendered_text(report)
        blocks: list[dict[str, Any]] = [
            {
                "type": "paragraph",
                "text": rendered_text or "No report text available.",
            }
        ]

        if frame_details:
            blocks.append(
                {
                    "type": "sentence_group",
                    "section_title": "AI prediction frames",
                    "variables": {},
                    "sentences": [
                        {
                            "template": str(detail["caption"]),
                            "enabled": True,
                        }
                        for detail in frame_details
                    ],
                }
            )

        return blocks

    def _get_or_create_selection_report(
        self,
        *,
        patient_examination: PatientExamination,
        report_id: int | None,
        template_name: str | None = None,
    ) -> tuple[PatientExaminationReport, bool]:
        if report_id is not None:
            report = (
                self._apply_center_scope(
                    PatientExaminationReport.objects.select_related(
                        "patient_examination__patient"
                    )
                )
                .filter(pk=report_id, patient_examination=patient_examination)
                .first()
            )
            if report is None:
                raise HttpError(403, "Report not found for this patient examination.")
            return report, False

        existing = (
            self._apply_center_scope(
                PatientExaminationReport.objects.select_related(
                    "patient_examination__patient"
                )
            )
            .filter(patient_examination=patient_examination, is_active=True)
            .order_by("-updated_at", "-id")
            .first()
        )
        if existing is not None:
            return existing, False

        user = _authenticated_user_from_request(self.request)
        report = PatientExaminationReport.objects.create(
            patient_examination=patient_examination,
            template_name=template_name or "segment_frame_selection",
            title="Segment Frame Selection",
            status=PatientExaminationReport.Status.DRAFT,
            editor_payload={self.SEGMENT_FRAME_SELECTIONS_KEY: {}},
            created_by=user,
            updated_by=user,
        )
        return report, True

    def _resolve_segment_frame_number(
        self,
        *,
        segment: LabelVideoSegment,
        request_data: Mapping[str, Any],
        current_frame_number: int | None,
    ) -> tuple[int | None, str]:
        action = str(request_data.get("action", "set")).lower()

        if action == "clear":
            return None, "cleared"

        start_n = _segment_start(segment)
        end_n = _segment_end(segment)

        if action == "random":
            frame_numbers = list(
                segment.get_frames()
                .order_by("frame_number")
                .values_list("frame_number", flat=True)
            )
            if frame_numbers:
                return int(random.choice(frame_numbers)), "random"
            return random.randint(start_n, end_n), "random_segment_range"

        if action == "step":
            raw_step = request_data.get("step", 5)
            try:
                step = int(raw_step)
            except (TypeError, ValueError):
                step = 5

            base = (
                current_frame_number
                if current_frame_number is not None
                else self._segment_midpoint_frame(segment)
            )
            return max(start_n, min(end_n, base + step)), f"step_{step}"

        if "frame_number" not in request_data:
            return (
                (
                    current_frame_number
                    if current_frame_number is not None
                    else self._segment_midpoint_frame(segment)
                ),
                "default",
            )

        try:
            frame_number_raw = request_data.get("frame_number")
            if frame_number_raw is None:
                raise ValueError
            chosen = int(frame_number_raw)
        except (TypeError, ValueError):
            raise HttpError(400, "frame_number must be an integer")

        return max(start_n, min(end_n, chosen)), "set"

    def _resolve_patient_finding_for_segment(
        self,
        *,
        patient_examination: PatientExamination,
        segment: LabelVideoSegment,
        finding_id: int | None,
    ) -> tuple[PatientFinding | None, bool]:
        if finding_id is None:
            existing = (
                segment.patient_findings.filter(
                    patient_examination=patient_examination,
                    is_active=True,
                )
                .select_related("finding")
                .order_by("-updated_at", "-id")
                .first()
            )
            return existing, False

        finding = Finding.objects.filter(pk=finding_id).first()
        if finding is None:
            raise HttpError(400, "finding_id does not exist")

        user = _authenticated_user_from_request(self.request)
        patient_finding = (
            PatientFinding.objects.filter(
                patient_examination=patient_examination,
                finding=finding,
                is_active=True,
            )
            .select_related("finding")
            .first()
        )

        created = False
        if patient_finding is None:
            patient_finding = PatientFinding(
                patient_examination=patient_examination,
                finding=finding,
                created_by=user,
                updated_by=user,
                is_active=True,
            )
            cast(Any, patient_finding).save()
            created = True
        else:
            user_id = getattr(user, "id", None)
            if user and getattr(patient_finding, "updated_by_id", None) != user_id:
                _assign_patient_finding_updated_by(patient_finding, user)
                patient_finding.save(update_fields=["updated_by", "updated_at"])

        segment.patient_findings.add(patient_finding)
        return patient_finding, created

    def _serialize_segment_frame_item(
        self,
        *,
        patient_examination: PatientExamination,
        segment: LabelVideoSegment,
        selection_map: ReportSegmentSelectionMap,
    ) -> SegmentFrameSelectorItemData:
        segment_id = _segment_pk(segment)
        selection = selection_map.get(str(segment_id), {})

        stored_frame_number = selection.get("frame_number")
        if isinstance(stored_frame_number, str) and stored_frame_number.isdigit():
            stored_frame_number = int(stored_frame_number)
        if not isinstance(stored_frame_number, int):
            stored_frame_number = None

        patient_finding = None
        for pf in segment.patient_findings.all():
            if (
                getattr(pf, "patient_examination_id") == patient_examination.pk
                and pf.is_active
            ):
                patient_finding = pf
                break

        if patient_finding is None:
            pf_id = selection.get("patient_finding_id")
            if pf_id:
                patient_finding = (
                    PatientFinding.objects.filter(
                        pk=pf_id,
                        patient_examination=patient_examination,
                        is_active=True,
                    )
                    .select_related("finding")
                    .first()
                )

        frame_qs = segment.get_frames().order_by("frame_number")
        available_frame_numbers = [
            int(frame_number)
            for frame_number in frame_qs.values_list("frame_number", flat=True)
        ]

        if stored_frame_number is not None and available_frame_numbers:
            selected_frame_number = max(
                _segment_start(segment),
                min(_segment_end(segment), stored_frame_number),
            )
        else:
            selected_frame_number = stored_frame_number or (
                available_frame_numbers[0]
                if available_frame_numbers
                else self._segment_midpoint_frame(segment)
            )

        selected_frame = frame_qs.filter(frame_number=selected_frame_number).first()

        frame_preview: SegmentFramePreviewData | None = None
        if selected_frame is not None:
            selected_frame_number_value = _frame_number(selected_frame)
            frame_stream_path = build_video_frame_stream_path(
                _segment_video_id(segment),
                selected_frame_number_value,
            )
            frame_preview = {
                "frame_id": _frame_pk(selected_frame),
                "frame_number": selected_frame_number_value,
                "timestamp": _frame_timestamp(selected_frame),
                "relative_path": str(getattr(selected_frame, "relative_path", "")),
                "file_exists": bool(getattr(selected_frame, "is_extracted", False)),
                "stream_url": self.request.build_absolute_uri(frame_stream_path),
            }

        midpoint = self._segment_midpoint_frame(segment)
        random_candidate = (
            int(random.choice(available_frame_numbers))
            if available_frame_numbers
            else random.randint(_segment_start(segment), _segment_end(segment))
        )

        return {
            "segment_id": segment_id,
            "video_id": _segment_video_id(segment),
            "label_id": _segment_label_id(segment),
            "label_name": _segment_label_name(segment),
            "start_frame_number": _segment_start(segment),
            "end_frame_number": _segment_end(segment),
            "segment_duration_seconds": _segment_duration(segment),
            "selected_frame_number": selected_frame_number,
            "selected_frame": frame_preview,
            "controls": {
                "random_frame_number": random_candidate,
                "step_backward_5_frame_number": max(
                    _segment_start(segment),
                    (selected_frame_number or midpoint) - 5,
                ),
                "step_forward_5_frame_number": min(
                    _segment_end(segment),
                    (selected_frame_number or midpoint) + 5,
                ),
            },
            "attached_finding": (
                {
                    "patient_finding_id": _patient_finding_pk(patient_finding),
                    "finding_id": _patient_finding_finding_id(patient_finding),
                    "finding_name": _patient_finding_finding_name(patient_finding),
                }
                if patient_finding is not None
                else None
            ),
            "selection_meta": {
                "updated_at": selection.get("updated_at"),
                "selection_source": (
                    selection.get("selection_source", "stored") if selection else None
                ),
            },
        }

    def build_segment_frame_selector_response(
        self,
        *,
        patient_examination: PatientExamination,
        report: PatientExaminationReport,
        auto_created: bool,
    ) -> SegmentFrameSelectorResponseData:
        try:
            selection_map = self._get_segment_selection_map(report)
        except ValueError as exc:
            raise HttpError(409, str(exc)) from exc

        segments = (
            LabelVideoSegment.objects.select_related("video_file", "label")
            .prefetch_related("patient_findings__finding")
            .filter(self._patient_examination_segment_filter(patient_examination))
            .distinct()
            .order_by("video_file_id", "start_frame_number", "id")
        )

        items: list[SegmentFrameSelectorItemData] = [
            self._serialize_segment_frame_item(
                patient_examination=patient_examination,
                segment=segment,
                selection_map=selection_map,
            )
            for segment in segments
        ]

        return {
            "patient_examination_id": _patient_examination_pk(patient_examination),
            "report_id": _report_pk(report),
            "report_status": _report_status(report),
            "report_template_name": _report_template_name(report),
            "auto_created_report": auto_created,
            "storage_key": self.SEGMENT_FRAME_SELECTIONS_KEY,
            "count": len(items),
            "results": items,
        }


@router.get(
    "/",
    response=list[PatientExaminationReportSchema],
)
def list_reports(
    request: HttpRequest,
    patient_examination_id: int | None = _ninja_query(default=None),
    status: str | None = _ninja_query(default=None),
    template_name: str | None = _ninja_query(default=None),
    is_active: bool | None = _ninja_query(default=None),
) -> QuerySet[PatientExaminationReport]:
    api = PatientExaminationReportApi(request)
    return api.get_queryset(
        patient_examination_id=patient_examination_id,
        status=status,
        template_name=template_name,
        is_active=is_active,
        broad_list=True,
    )


@router.post(
    "/save-submission",
    response={200: dict[str, Any], 201: dict[str, Any]},
)
def save_submission(
    request: HttpRequest,
) -> tuple[int, dict[str, Any]]:
    api = PatientExaminationReportApi(request)
    try:
        payload = PatientExaminationReportSubmissionPayload.model_validate(
            _request_json_object(request)
        )
    except ValidationError as exc:
        raise HttpError(422, json.dumps(exc.errors())) from exc
    data: PatientExaminationReportSubmissionData = dump_report_submission_payload(
        payload
    )

    api._get_scoped_patient_examination(data["patient_examination_id"])

    result = save_report_submission(
        patient_examination_id=data["patient_examination_id"],
        template_name=data["template_name"],
        editor_payload=data.get("editor_payload"),
        rendered_text=data.get("rendered_text", ""),
        status=data.get("status", PatientExaminationReport.Status.DRAFT),
        user=_authenticated_user_from_request(request),
        report_id=data.get("report_id"),
        expected_version=data.get("expected_version"),
        patient_data=data.get("patient_data"),
        indications=data.get("indications"),
        findings=data.get("findings"),
        title=data.get("title", ""),
        template_version=data.get("template_version", ""),
        template_hash=data.get("template_hash", ""),
        history_limit=data.get("history_limit", 5),
    )

    persisted_artifacts = api._build_persisted_artifacts_payload(
        patient_examination=_report_patient_examination(result.report),
        persisted_report_artifact_id=result.persisted_report_artifact_id,
        persisted_pdf_artifact_id=result.persisted_pdf_artifact_id,
    )

    persisted_dtypes_record = (
        report_json_safe_dict(result.persisted_dtypes_record)
        if result.persisted_dtypes_record is not None
        else None
    )
    persisted_dtypes_record_updated_at = (
        report_json_safe(result.persisted_dtypes_record_updated_at)
        if result.persisted_dtypes_record_updated_at is not None
        else None
    )

    response_data: dict[str, Any] = {
        "report": _serialized_report_data(result.report),
        "created": result.created,
        "warnings": result.warnings,
        "history_context": result.history_context,
        "persisted_dtypes_record": persisted_dtypes_record,
        "persisted_dtypes_record_updated_at": persisted_dtypes_record_updated_at,
        "persisted_report_artifact_id": result.persisted_report_artifact_id,
        "persisted_pdf_artifact_id": result.persisted_pdf_artifact_id,
        "persisted_artifacts": persisted_artifacts,
    }

    return (201 if result.created else 200), response_data


@router.post(
    "/make-report",
    response={200: dict[str, Any], 404: dict[str, str], 500: dict[str, str]},
)
def make_report(
    request: HttpRequest,
) -> tuple[int, dict[str, Any]]:
    api = PatientExaminationReportApi(request)
    try:
        payload = PatientExaminationReportMakeReportPayload.model_validate(
            _request_json_object(request)
        )
    except ValidationError as exc:
        raise HttpError(422, json.dumps(exc.errors())) from exc
    data: PatientExaminationReportMakeReportData = dump_make_report_payload(payload)

    patient_examination = api._get_scoped_patient_examination(
        data["patient_examination_id"]
    )
    report = api._resolve_report_for_export(
        patient_examination=patient_examination,
        report_id=data.get("report_id"),
    )
    if report is None:
        return 404, {"detail": "No report found for this patient examination."}

    user = _authenticated_user_from_request(request)
    if _report_status(report) != "final":
        report.status = "final"
        report.finalized_at = timezone.now()
        _assign_report_user_fields(
            report,
            finalized_by=user,
            updated_by=user,
        )
        report.save(
            update_fields=[
                "status",
                "finalized_at",
                "finalized_by",
                "updated_by",
                "updated_at",
            ]
        )

    (
        frame_paths,
        frame_captions,
        frame_details,
        frame_warnings,
    ) = api._collect_report_export_frames(
        patient_examination=patient_examination,
        report=report,
        max_frames=data["max_frames"],
    )

    section_blocks = api._build_report_export_blocks(
        report=report,
        frame_details=frame_details,
    )

    try:
        (
            persisted_report_artifact_id,
            persisted_pdf_artifact_id,
        ) = persist_report_pdf_artifact(
            report,
            patient_examination,
            rendered_text=_report_rendered_text(report),
            section_blocks=section_blocks,
            frame_image_paths=frame_paths,
            frame_captions=frame_captions,
            patient_identity=dict(data["patient"]),
            strict_renderer=True,
        )
    except Exception as exc:
        return 500, {"detail": f"PDF report generation failed ({type(exc).__name__})."}

    persisted_artifacts = api._build_persisted_artifacts_payload(
        patient_examination=patient_examination,
        persisted_report_artifact_id=persisted_report_artifact_id,
        persisted_pdf_artifact_id=persisted_pdf_artifact_id,
    )

    return 200, {
        "report": _serialized_report_data(report),
        "warnings": frame_warnings,
        "included_frame_count": len(frame_details),
        "included_frames": frame_details,
        "persisted_report_artifact_id": persisted_report_artifact_id,
        "persisted_pdf_artifact_id": persisted_pdf_artifact_id,
        "persisted_artifacts": persisted_artifacts,
    }


@router.get(
    "/segment-frame-selector",
    response=SegmentFrameSelectorResponseSchema,
)
def get_segment_frame_selector(
    request: HttpRequest,
) -> SegmentFrameSelectorResponseData:
    api = PatientExaminationReportApi(request)
    try:
        selector_query = SegmentFrameSelectorQueryPayload.model_validate(
            {
                "patient_examination_id": request.GET.get("patient_examination_id"),
                "report_id": request.GET.get("report_id"),
            }
        )
    except ValidationError as exc:
        raise HttpError(422, json.dumps(exc.errors())) from exc

    patient_examination = api._get_scoped_patient_examination(
        selector_query.patient_examination_id
    )
    report, auto_created = api._get_or_create_selection_report(
        patient_examination=patient_examination,
        report_id=selector_query.report_id,
    )

    return api.build_segment_frame_selector_response(
        patient_examination=patient_examination,
        report=report,
        auto_created=auto_created,
    )


@router.patch(
    "/segment-frame-selector",
    response=SegmentFrameSelectorResponseSchema,
)
def patch_segment_frame_selector(
    request: HttpRequest,
) -> SegmentFrameSelectorResponseData:
    api = PatientExaminationReportApi(request)
    try:
        selector_patch = SegmentFrameSelectorPatchPayload.model_validate(
            _request_json_object(request)
        )
    except ValidationError as exc:
        raise HttpError(422, json.dumps(exc.errors())) from exc
    data = dump_selector_patch_payload(selector_patch)

    patient_examination = api._get_scoped_patient_examination(
        selector_patch.patient_examination_id
    )

    report, auto_created = api._get_or_create_selection_report(
        patient_examination=patient_examination,
        report_id=selector_patch.report_id,
        template_name=selector_patch.template_name,
    )

    segment = (
        LabelVideoSegment.objects.select_related("video_file", "label")
        .filter(api._patient_examination_segment_filter(patient_examination))
        .distinct()
        .filter(pk=selector_patch.segment_id)
        .first()
    )
    if segment is None:
        raise HttpError(404, "Segment not found for this patient examination.")

    try:
        selection_map = api._get_segment_selection_map(report)
    except ValueError as exc:
        raise HttpError(409, str(exc)) from exc

    current = selection_map.get(str(_segment_pk(segment)), {})
    current_frame_number = (
        current.get("frame_number")
        if isinstance(current.get("frame_number"), int)
        else None
    )

    frame_number, selection_source = api._resolve_segment_frame_number(
        segment=segment,
        request_data=data,
        current_frame_number=current_frame_number,
    )

    patient_finding, _ = api._resolve_patient_finding_for_segment(
        patient_examination=patient_examination,
        segment=segment,
        finding_id=selector_patch.finding_id,
    )

    segment_pk = _segment_pk(segment)
    video_id = _segment_video_id(segment)

    if frame_number is None:
        api._persist_segment_selection(
            report=report,
            segment_id=segment_pk,
            selection_value=None,
        )
    else:
        selected_frame = Frame.objects.filter(
            video_id=video_id,
            frame_number=frame_number,
        ).first()

        selection_payload = ReportSegmentFrameSelectionPayload(
            segment_id=segment_pk,
            video_id=video_id,
            frame_number=int(frame_number),
            frame_id=optional_model_pk(selected_frame),
            relative_path=(
                _frame_relative_path(selected_frame)
                if selected_frame is not None
                else None
            ),
            finding_id=(
                _patient_finding_finding_id(patient_finding)
                if patient_finding is not None
                else None
            ),
            patient_finding_id=(
                _patient_finding_pk(patient_finding)
                if patient_finding is not None
                else None
            ),
            updated_at=timezone.now().isoformat(),
            selection_source=selection_source,
        )

        api._persist_segment_selection(
            report=report,
            segment_id=segment_pk,
            selection_value=dump_segment_frame_selection_payload(selection_payload),
        )

    report.refresh_from_db()

    return api.build_segment_frame_selector_response(
        patient_examination=patient_examination,
        report=report,
        auto_created=auto_created,
    )


@router.get(
    "/history-context",
    response=dict[str, Any],
)
def history_context(
    request: HttpRequest,
    patient_examination_id: int = _ninja_query(...),
    limit: int = _ninja_query(default=5, ge=1, le=50),
) -> dict[str, Any]:
    api = PatientExaminationReportApi(request)
    patient_examination = api._get_scoped_patient_examination(patient_examination_id)

    return cast(
        dict[str, Any],
        get_patient_examination_history_context(
            patient_examination,
            limit=max(1, min(limit, 50)),
        ),
    )


@router.get(
    "/{report_id}",
    response=PatientExaminationReportSchema,
)
def get_report(
    request: HttpRequest,
    report_id: int,
) -> PatientExaminationReport:
    api = PatientExaminationReportApi(request)
    report = api.get_queryset().filter(pk=report_id).first()
    if report is None:
        raise HttpError(404, "Report not found.")
    return report
