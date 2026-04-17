from __future__ import annotations

import random
from copy import deepcopy

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.models import (
    Finding,
    Frame,
    LabelVideoSegment,
    PatientExamination,
    PatientExaminationReport,
    PatientFinding,
)
from endoreg_db.serializers.report import (
    PatientExaminationReportSerializer,
    PatientExaminationReportSubmissionSerializer,
)
from endoreg_db.services.report_history import get_patient_examination_history_context
from endoreg_db.services.report_persistence import save_report_submission
from endoreg_db.utils.media_urls import (
    build_absolute_media_url,
    build_patient_timeline_path,
    build_pdf_stream_path,
)


class PatientExaminationReportViewSet(viewsets.ModelViewSet):
    SEGMENT_FRAME_SELECTIONS_KEY = "report_segment_frame_selections"

    queryset = PatientExaminationReport.objects.all().select_related(
        "patient_examination",
        "created_by",
        "updated_by",
        "finalized_by",
    )
    serializer_class = PatientExaminationReportSerializer
    permission_classes = [PolicyPermission]
    filterset_fields = ["patient_examination", "status", "template_name", "is_active"]

    def _is_privileged_user(self, user) -> bool:
        return bool(
            user
            and getattr(user, "is_authenticated", False)
            and (
                getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)
            )
        )

    def _allowed_center_ids_for_user(self, user) -> set[int] | None:
        """
        Return allowed center IDs for this user.

        - `None` means unrestricted (staff/superuser).
        - Empty set means no scoped center access could be determined.
        """
        if self._is_privileged_user(user):
            return None
        if not user or not getattr(user, "is_authenticated", False):
            return set()

        center_ids: set[int] = set()
        portal_user_info = getattr(user, "portaluserinfo", None)
        examiner = (
            getattr(portal_user_info, "examiner", None) if portal_user_info else None
        )
        center_id = getattr(examiner, "center_id", None) if examiner else None
        if isinstance(center_id, int):
            center_ids.add(center_id)
        return center_ids

    def _apply_center_scope(self, queryset):
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
        self, patient_examination_id: int
    ) -> PatientExamination:
        queryset = PatientExamination.objects.select_related("patient", "examination")
        queryset = self._apply_center_scope(queryset)
        patient_examination = queryset.filter(pk=patient_examination_id).first()
        if patient_examination is None:
            raise PermissionDenied(
                "You do not have access to this patient examination or it does not exist."
            )
        return patient_examination

    def get_queryset(self):
        queryset = self._apply_center_scope(super().get_queryset())
        patient_examination_id = self.request.query_params.get("patient_examination_id")
        if patient_examination_id:
            queryset = queryset.filter(patient_examination_id=patient_examination_id)
        elif self.action == "list" and not self._is_privileged_user(
            getattr(self.request, "user", None)
        ):
            # Prevent broad report listing for non-privileged users without explicit scoping.
            queryset = queryset.none()
        return queryset.order_by("-updated_at", "-id")

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
                raise PermissionDenied("Report not found for this patient examination.")
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

        user = (
            self.request.user
            if getattr(self.request, "user", None)
            and self.request.user.is_authenticated
            else None
        )
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

    @staticmethod
    def _get_segment_selection_map(report: PatientExaminationReport) -> dict[str, dict]:
        payload = report.editor_payload or {}
        selections = payload.get(
            PatientExaminationReportViewSet.SEGMENT_FRAME_SELECTIONS_KEY
        )
        if isinstance(selections, dict):
            return selections
        return {}

    def _persist_segment_selection(
        self,
        *,
        report: PatientExaminationReport,
        segment_id: int,
        selection_value: dict | None,
    ) -> None:
        payload = deepcopy(report.editor_payload or {})
        selections = payload.get(self.SEGMENT_FRAME_SELECTIONS_KEY)
        if not isinstance(selections, dict):
            selections = {}
        key = str(segment_id)
        if selection_value is None:
            selections.pop(key, None)
        else:
            selections[key] = selection_value
        payload[self.SEGMENT_FRAME_SELECTIONS_KEY] = selections
        report.editor_payload = payload
        report.updated_by = (
            self.request.user
            if getattr(self.request, "user", None)
            and self.request.user.is_authenticated
            else report.updated_by
        )
        report.save(update_fields=["editor_payload", "updated_by", "updated_at"])

    @staticmethod
    def _segment_midpoint_frame(segment: LabelVideoSegment) -> int:
        return int((segment.start_frame_number + segment.end_frame_number) // 2)

    def _resolve_segment_frame_number(
        self,
        *,
        segment: LabelVideoSegment,
        request_data: dict,
        current_frame_number: int | None,
    ) -> tuple[int | None, str]:
        action = str(request_data.get("action", "set")).lower()
        if action == "clear":
            return None, "cleared"

        start_n = int(segment.start_frame_number)
        end_n = int(segment.end_frame_number)

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

        # default / explicit set
        if "frame_number" not in request_data:
            return (
                current_frame_number
                if current_frame_number is not None
                else self._segment_midpoint_frame(segment),
                "default",
            )
        try:
            frame_number_raw = request_data.get("frame_number")
            if frame_number_raw is None:
                raise ValueError
            chosen = int(frame_number_raw)
        except (TypeError, ValueError):
            raise ValueError("frame_number must be an integer")
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
                    patient_examination=patient_examination, is_active=True
                )
                .select_related("finding")
                .order_by("-updated_at", "-id")
                .first()
            )
            return existing, False

        finding = Finding.objects.filter(pk=finding_id).first()
        if finding is None:
            raise ValueError("finding_id does not exist")

        user = (
            self.request.user
            if getattr(self.request, "user", None)
            and self.request.user.is_authenticated
            else None
        )
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
            patient_finding.save()
            created = True
        else:
            if user and patient_finding.updated_by_id != user.id:
                patient_finding.updated_by = user
                patient_finding.save(update_fields=["updated_by", "updated_at"])

        segment.patient_findings.add(patient_finding)
        return patient_finding, created

    def _serialize_segment_frame_item(
        self,
        *,
        patient_examination: PatientExamination,
        segment: LabelVideoSegment,
        selection_map: dict[str, dict],
    ) -> dict:
        selection = (
            selection_map.get(str(segment.pk), {})
            if isinstance(selection_map, dict)
            else {}
        )
        stored_frame_number = selection.get("frame_number")
        if isinstance(stored_frame_number, str) and stored_frame_number.isdigit():
            stored_frame_number = int(stored_frame_number)
        if not isinstance(stored_frame_number, int):
            stored_frame_number = None

        patient_finding = (
            segment.patient_findings.filter(
                patient_examination=patient_examination, is_active=True
            )
            .select_related("finding")
            .order_by("-updated_at", "-id")
            .first()
        )
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
        available_frame_numbers = list(frame_qs.values_list("frame_number", flat=True))
        if stored_frame_number is not None and available_frame_numbers:
            selected_frame_number = max(
                segment.start_frame_number,
                min(segment.end_frame_number, stored_frame_number),
            )
        else:
            selected_frame_number = stored_frame_number or (
                available_frame_numbers[0]
                if available_frame_numbers
                else self._segment_midpoint_frame(segment)
            )

        selected_frame = (
            frame_qs.filter(frame_number=selected_frame_number).first()
            if selected_frame_number is not None
            else None
        )
        frame_preview = None
        if selected_frame is not None:
            frame_stream_path = (
                f"/api/media/videos/{segment.video_file_id}/frames/"
                f"{selected_frame.frame_number}/stream/"
            )
            frame_preview = {
                "frame_id": selected_frame.pk,
                "frame_number": selected_frame.frame_number,
                "timestamp": selected_frame.timestamp,
                "relative_path": selected_frame.relative_path,
                "file_exists": bool(selected_frame.is_extracted),
                "stream_url": self.request.build_absolute_uri(frame_stream_path),
            }
        midpoint = self._segment_midpoint_frame(segment)
        random_candidate = (
            int(random.choice(available_frame_numbers))
            if available_frame_numbers
            else random.randint(segment.start_frame_number, segment.end_frame_number)
        )
        step_back = max(
            segment.start_frame_number, (selected_frame_number or midpoint) - 5
        )
        step_forward = min(
            segment.end_frame_number, (selected_frame_number or midpoint) + 5
        )

        return {
            "segment_id": segment.pk,
            "video_id": segment.video_file_id,
            "label_id": segment.label_id,
            "label_name": getattr(segment.label, "name", None),
            "start_frame_number": segment.start_frame_number,
            "end_frame_number": segment.end_frame_number,
            "segment_duration_seconds": segment.segment_duration,
            "selected_frame_number": selected_frame_number,
            "selected_frame": frame_preview,
            "controls": {
                "random_frame_number": random_candidate,
                "step_backward_5_frame_number": step_back,
                "step_forward_5_frame_number": step_forward,
            },
            "attached_finding": (
                {
                    "patient_finding_id": patient_finding.pk,
                    "finding_id": patient_finding.finding_id,
                    "finding_name": getattr(patient_finding.finding, "name", None),
                }
                if patient_finding is not None
                else None
            ),
            "selection_meta": {
                "updated_at": selection.get("updated_at"),
                "selection_source": selection.get("selection_source", "stored")
                if selection
                else None,
            },
        }

    @action(detail=False, methods=["post"], url_path="save-submission")
    def save_submission(self, request):
        payload_serializer = PatientExaminationReportSubmissionSerializer(
            data=request.data
        )
        payload_serializer.is_valid(raise_exception=True)
        payload = payload_serializer.validated_data

        self._get_scoped_patient_examination(payload["patient_examination_id"])

        result = save_report_submission(
            patient_examination_id=payload["patient_examination_id"],
            template_name=payload["template_name"],
            editor_payload=payload.get("editor_payload"),
            rendered_text=payload.get("rendered_text", ""),
            status=payload.get("status", PatientExaminationReport.Status.DRAFT),
            user=request.user
            if getattr(request, "user", None) and request.user.is_authenticated
            else None,
            report_id=payload.get("report_id"),
            expected_version=payload.get("expected_version"),
            patient_data=payload.get("patient_data"),
            indications=payload.get("indications"),
            findings=payload.get("findings"),
            title=payload.get("title", ""),
            template_version=payload.get("template_version", ""),
            template_hash=payload.get("template_hash", ""),
            history_limit=payload.get("history_limit", 5),
        )

        persisted_artifacts = None
        if result.persisted_pdf_artifact_id or result.persisted_report_artifact_id:
            patient_examination = result.report.patient_examination
            patient_id = getattr(patient_examination, "patient_id", None)
            pdf_id = result.persisted_pdf_artifact_id
            persisted_artifacts = {
                "full_report_id": result.persisted_report_artifact_id,
                "pdf_id": result.persisted_pdf_artifact_id,
                "pdf_view_url": (
                    build_absolute_media_url(
                        request,
                        build_pdf_stream_path(pdf_id, file_type="processed"),
                    )
                    if pdf_id is not None
                    else None
                ),
                "pdf_download_url": (
                    build_absolute_media_url(
                        request,
                        build_pdf_stream_path(
                            pdf_id,
                            file_type="raw",
                            download=True,
                        ),
                    )
                    if pdf_id is not None
                    else None
                ),
                "patient_timeline_url": (
                    build_absolute_media_url(
                        request,
                        build_patient_timeline_path(patient_id),
                    )
                    if patient_id is not None
                    else None
                ),
            }

        response_data = {
            "report": PatientExaminationReportSerializer(result.report).data,
            "created": result.created,
            "warnings": result.warnings,
            "history_context": result.history_context,
            "persisted_report_artifact_id": result.persisted_report_artifact_id,
            "persisted_pdf_artifact_id": result.persisted_pdf_artifact_id,
            "persisted_artifacts": persisted_artifacts,
        }
        return Response(
            response_data,
            status=status.HTTP_201_CREATED if result.created else status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get", "patch"], url_path="segment-frame-selector")
    def segment_frame_selector(self, request):
        pe_raw = (
            request.query_params.get("patient_examination_id")
            if request.method == "GET"
            else request.data.get("patient_examination_id")
        )
        if not pe_raw:
            return Response(
                {"detail": "patient_examination_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            pe_id = int(pe_raw)
        except (TypeError, ValueError):
            return Response(
                {"detail": "patient_examination_id must be an integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            patient_examination = self._get_scoped_patient_examination(pe_id)
        except PermissionDenied as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)

        report_id_raw = (
            request.query_params.get("report_id")
            if request.method == "GET"
            else request.data.get("report_id")
        )
        report_id: int | None
        if report_id_raw in (None, ""):
            report_id = None
        else:
            try:
                report_id = int(report_id_raw)
            except (TypeError, ValueError):
                return Response(
                    {"detail": "report_id must be an integer."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        template_name = (
            request.data.get("template_name") if request.method == "PATCH" else None
        )
        report, auto_created = self._get_or_create_selection_report(
            patient_examination=patient_examination,
            report_id=report_id,
            template_name=template_name if isinstance(template_name, str) else None,
        )

        if request.method == "PATCH":
            segment_id = request.data.get("segment_id")
            if segment_id in (None, ""):
                return Response(
                    {"detail": "segment_id is required for PATCH."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            try:
                segment_id_int = int(segment_id)
            except (TypeError, ValueError):
                return Response(
                    {"detail": "segment_id must be an integer."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            segments_qs = (
                LabelVideoSegment.objects.select_related("video_file", "label")
                .filter(
                    Q(video_file__examination_id=patient_examination.id)
                    | Q(video_file_id=getattr(patient_examination, "video_id", None))
                )
                .distinct()
            )
            segment = segments_qs.filter(pk=segment_id_int).first()
            if segment is None:
                return Response(
                    {"detail": "Segment not found for this patient examination."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            selection_map = self._get_segment_selection_map(report)
            current = (
                selection_map.get(str(segment.pk), {})
                if isinstance(selection_map, dict)
                else {}
            )
            current_frame_number = (
                current.get("frame_number")
                if isinstance(current.get("frame_number"), int)
                else None
            )

            try:
                frame_number, selection_source = self._resolve_segment_frame_number(
                    segment=segment,
                    request_data=request.data,
                    current_frame_number=current_frame_number,
                )
            except ValueError as exc:
                return Response(
                    {"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST
                )

            finding_input = request.data.get("finding_id", "__unchanged__")
            try:
                if finding_input == "__unchanged__":
                    patient_finding, _ = self._resolve_patient_finding_for_segment(
                        patient_examination=patient_examination,
                        segment=segment,
                        finding_id=None,
                    )
                elif finding_input in ("", None):
                    patient_finding = None
                else:
                    patient_finding, _ = self._resolve_patient_finding_for_segment(
                        patient_examination=patient_examination,
                        segment=segment,
                        finding_id=int(finding_input),
                    )
            except (ValueError, TypeError) as exc:
                return Response(
                    {"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST
                )

            with transaction.atomic():
                if frame_number is None:
                    self._persist_segment_selection(
                        report=report,
                        segment_id=segment.pk,
                        selection_value=None,
                    )
                else:
                    selected_frame = Frame.objects.filter(
                        video_id=segment.video_file_id, frame_number=frame_number
                    ).first()
                    self._persist_segment_selection(
                        report=report,
                        segment_id=segment.pk,
                        selection_value={
                            "segment_id": segment.pk,
                            "video_id": segment.video_file_id,
                            "frame_number": int(frame_number),
                            "frame_id": getattr(selected_frame, "pk", None),
                            "relative_path": getattr(
                                selected_frame, "relative_path", None
                            ),
                            "finding_id": getattr(patient_finding, "finding_id", None),
                            "patient_finding_id": getattr(patient_finding, "pk", None),
                            "updated_at": timezone.now().isoformat(),
                            "selection_source": selection_source,
                        },
                    )
                report.refresh_from_db()

        selection_map = self._get_segment_selection_map(report)
        segments = (
            LabelVideoSegment.objects.select_related("video_file", "label")
            .prefetch_related("patient_findings__finding")
            .filter(
                Q(video_file__examination_id=patient_examination.id)
                | Q(video_file_id=getattr(patient_examination, "video_id", None))
            )
            .distinct()
            .order_by("video_file_id", "start_frame_number", "id")
        )

        items = [
            self._serialize_segment_frame_item(
                patient_examination=patient_examination,
                segment=segment,
                selection_map=selection_map,
            )
            for segment in segments
        ]

        return Response(
            {
                "patient_examination_id": patient_examination.id,
                "report_id": report.id,
                "report_status": report.status,
                "report_template_name": report.template_name,
                "auto_created_report": auto_created,
                "storage_key": self.SEGMENT_FRAME_SELECTIONS_KEY,
                "count": len(items),
                "results": items,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"], url_path="history-context")
    def history_context(self, request):
        patient_examination_id = request.query_params.get("patient_examination_id")
        if not patient_examination_id:
            return Response(
                {"detail": "patient_examination_id query parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            pe_id_int = int(patient_examination_id)
        except ValueError:
            return Response(
                {"detail": "patient_examination_id must be an integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            patient_examination = self._get_scoped_patient_examination(pe_id_int)
        except PermissionDenied as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)

        try:
            limit = int(request.query_params.get("limit", 5))
        except ValueError:
            return Response(
                {"detail": "limit must be an integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        history_payload = get_patient_examination_history_context(
            patient_examination, limit=max(1, min(limit, 50))
        )
        return Response(history_payload, status=status.HTTP_200_OK)
