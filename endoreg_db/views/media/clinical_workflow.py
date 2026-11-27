"""Workflow endpoints for lx-annotate.

Wire contract: lx-annotate ``docs/guides/media-workflow-frontend-contract.md``.
"""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from lx_dtypes.models.contracts.media_workflow_api import WorkflowInconsistency
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.openapi import OpenApiAPIView as APIView
from endoreg_db.services.centers.access import resolve_allowed_center_ids
from endoreg_db.services.clinical_lifecycle.facts import (
    report_record_facts,
    video_record_facts,
)
from endoreg_db.services.clinical_lifecycle.persistence import (
    WorkflowStateDriftError,
    read_workflow_state,
)
from endoreg_db.services.clinical_lifecycle.state_machine import (
    ClinicalLifecycleInvariantError,
    ClinicalRecordFacts,
    ClinicalRecordKind,
)
from endoreg_db.services.clinical_lifecycle.worklist import (
    allowed_events,
    stage_counts,
)
from endoreg_db.views.access_control import (
    assert_center_scope_allowed,
    filter_video_read_queryset,
)


# 409 violation token for a stored workflow row that disagrees with the facts.
STORED_STATE_DRIFT = "stored_state_drift"


class ClinicalWorkflowView(APIView):
    """Workflow state and reducer-accepted next events of one media record."""

    permission_classes = [IsAuthenticated, PolicyPermission]
    record_kind: ClinicalRecordKind = ClinicalRecordKind.VIDEO

    def _facts(self, request: Request, pk: int) -> ClinicalRecordFacts:
        match self.record_kind:
            case ClinicalRecordKind.VIDEO:
                video = get_object_or_404(
                    VideoFile.objects.select_related(
                        "center", "state", "sensitive_meta"
                    ),
                    pk=pk,
                )
                assert_center_scope_allowed(request=request, obj=video)
                return video_record_facts(video)
            case ClinicalRecordKind.REPORT:
                report = get_object_or_404(
                    RawPdfFile.objects.select_related(
                        "center", "state", "sensitive_meta"
                    ),
                    pk=pk,
                )
                assert_center_scope_allowed(request=request, obj=report)
                return report_record_facts(report)

    def _conflict(self, pk: int, violations: tuple[str, ...]) -> Response:
        body = WorkflowInconsistency(
            kind=self.record_kind, record_id=pk, violations=violations
        )
        return Response(body.model_dump(mode="json"), status=status.HTTP_409_CONFLICT)

    def get(self, request: Request, pk: int) -> Response:
        facts = self._facts(request, pk)
        try:
            snapshot = read_workflow_state(self.record_kind, pk, facts)
        except ClinicalLifecycleInvariantError as exc:
            # Inconsistent persisted facts are surfaced, never mapped to a state.
            return self._conflict(
                pk, tuple(violation.value for violation in exc.violations)
            )
        except WorkflowStateDriftError:
            # A stored row that disagrees with the facts is equally inconsistent.
            return self._conflict(pk, (STORED_STATE_DRIFT,))
        # Hand-built: the frontend contract lists reducer-accepted event tokens,
        # while lx-dtypes MediaWorkflowItem requires export_approval and offers
        # user-origin AllowedEvent objects only; adopting it changes the wire.
        return Response(
            {
                "kind": snapshot.kind.value,
                "record_id": snapshot.record_id,
                "privacy": snapshot.state.privacy.value,
                "annotation": snapshot.state.annotation.value,
                "release": snapshot.state.release.value,
                "version": snapshot.version,
                "source": snapshot.source.value,
                "allowed_events": [
                    event.value for event in allowed_events(snapshot.state, facts)
                ],
            }
        )


class ClinicalWorkflowStagesView(APIView):
    """Worklist counts per actionable stage over the caller's centers."""

    permission_classes = [IsAuthenticated, PolicyPermission]

    def get(self, request: Request) -> Response:
        videos = filter_video_read_queryset(
            queryset=VideoFile.objects.select_related(
                "center", "state", "sensitive_meta"
            ),
            user=request.user,
        )
        reports = RawPdfFile.objects.select_related("center", "state", "sensitive_meta")
        allowed_center_ids = resolve_allowed_center_ids(request.user)
        if allowed_center_ids == frozenset():
            reports = reports.none()
        elif allowed_center_ids is not None:
            reports = reports.filter(center_id__in=allowed_center_ids)
        counts = stage_counts(videos, reports)
        return Response(
            {
                **counts.stages.model_dump(mode="json"),
                "unmappable": counts.unmappable,
            }
        )
