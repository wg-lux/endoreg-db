from __future__ import annotations

import logging

from django.core.exceptions import ValidationError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.utils.permissions import EnvironmentAwarePermission
from endoreg_db.views.access_control import assert_center_scope_allowed

from ...models import RawPdfFile, ReportLlmInferenceJob
from endoreg_db.services.report_llm_jobs import (
    dispatch_report_llm_reimport,
    report_llm_job_payload,
)

logger = logging.getLogger(__name__)


class ReportReimportView(APIView):
    """
    Queue report re-import work outside the Daphne request process.
    """

    permission_classes = [EnvironmentAwarePermission, PolicyPermission]

    def post(self, request, pk):
        pdf_id = pk
        if not pdf_id or not isinstance(pdf_id, int):
            return Response(
                {"error": "Invalid report ID provided."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            pdf = RawPdfFile.objects.get(id=pdf_id)
            logger.info("Found report %s (ID: %s) for re-import", pdf.pdf_hash, pdf_id)
        except RawPdfFile.DoesNotExist:
            logger.warning("Report with ID %s not found", pdf_id)
            return Response(
                {"error": f"Report with ID {pdf_id} not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        assert_center_scope_allowed(
            request=request,
            obj=pdf,
            not_found_message="Report not found",
        )

        if not pdf.file or not getattr(pdf.file, "name", None):
            logger.error(
                "Raw report file not found for hash %s: missing storage file",
                pdf.pdf_hash,
            )
            return Response(
                {
                    "status": "failed",
                    "operation": "report_llm_reimport",
                    "reason": "missing_source",
                    "error": (
                        "Raw report file not found. Upload the original file again "
                        "before re-importing."
                    ),
                    "error_type": "missing_source",
                    "report_id": pdf_id,
                    "pdf_id": pdf_id,
                    "pdf_hash": str(pdf.pdf_hash),
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        if not pdf.center:
            logger.warning("Report %s has no associated center", pdf.pdf_hash)
            return Response(
                {"error": "Report has no associated center."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        request_data = getattr(request, "data", {})
        payload = request_data if hasattr(request_data, "get") else {}
        dispatch_result = dispatch_report_llm_reimport(
            report_id=pdf_id,
            payload=payload,
        )
        response_payload = {
            **dispatch_result.to_dict(),
            "pdf_id": pdf_id,
            "pdf_hash": str(pdf.pdf_hash),
        }

        if dispatch_result.status == "lost":
            return Response(
                {
                    **response_payload,
                    "error": "Report source is missing.",
                    "error_type": "missing_source",
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        if dispatch_result.status == "failed":
            return Response(
                {
                    **response_payload,
                    "error": "Report re-import dispatch failed.",
                    "error_type": "dispatch_error",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        if dispatch_result.status == "completed":
            return Response(
                {
                    **response_payload,
                    "message": "Report re-import completed.",
                },
                status=status.HTTP_200_OK,
            )

        return Response(
            {
                **response_payload,
                "message": (
                    "Report re-import is already queued."
                    if dispatch_result.status == "already_queued"
                    else "Report re-import queued."
                ),
            },
            status=status.HTTP_202_ACCEPTED,
        )


class ReportLlmJobStatusView(APIView):
    """
    Poll status for report LLM import/reimport jobs.
    """

    permission_classes = [EnvironmentAwarePermission, PolicyPermission]

    def get(self, request, pk: int, job_id: str):
        try:
            job = ReportLlmInferenceJob.objects.select_related(
                "pdf",
                "upload_job",
                "upload_job__source_center",
            ).get(
                pdf_id=pk,
                job_id=job_id,
            )
        except (ReportLlmInferenceJob.DoesNotExist, ValidationError, ValueError):
            return Response(
                {"error": "Report LLM job not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        assert_center_scope_allowed(
            request=request,
            obj=job,
            not_found_message="Report LLM job not found",
        )

        return Response(report_llm_job_payload(job), status=status.HTTP_200_OK)
