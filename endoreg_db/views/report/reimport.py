from __future__ import annotations

import logging
from typing import Any, cast

from django.core.exceptions import ValidationError
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.pdf.report_llm_job import ReportLlmInferenceJob
from endoreg_db.utils.permissions import EnvironmentAwarePermission
from endoreg_db.views.access_control import assert_center_scope_allowed
from endoreg_db.views.reimport_helpers import request_payload_dict

from endoreg_db.services.jobs.report_llm_jobs import (
    dispatch_report_llm_reimport,
    report_llm_job_payload,
)

logger = logging.getLogger(__name__)


def _pdf_hash(pdf: RawPdfFile) -> str:
    return str(cast(object, getattr(pdf, "pdf_hash", "")))


def _pdf_has_source_file(pdf: RawPdfFile) -> bool:
    file_field = cast(object, getattr(pdf, "file", None))
    return bool(file_field and getattr(file_field, "name", None))


def _pdf_center(pdf: RawPdfFile) -> object | None:
    return cast(object | None, getattr(pdf, "center", None))


class ReportReimportView(APIView):
    """
    Queue report re-import work outside the Daphne request process.
    """

    permission_classes = [EnvironmentAwarePermission, PolicyPermission]

    def post(self, request: Request, pk: object) -> Response:
        if not isinstance(pk, int) or pk <= 0:
            return Response(
                {"error": "Invalid report ID provided."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        pdf_id = pk

        try:
            pdf = RawPdfFile.objects.get(id=pdf_id)
            pdf_hash = _pdf_hash(pdf)
            logger.info("Found report %s (ID: %s) for re-import", pdf_hash, pdf_id)
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

        if not _pdf_has_source_file(pdf):
            logger.error(
                "Raw report file not found for hash %s: missing storage file",
                pdf_hash,
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
                    "pdf_hash": pdf_hash,
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        if _pdf_center(pdf) is None:
            logger.warning("Report %s has no associated center", pdf_hash)
            return Response(
                {"error": "Report has no associated center."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        payload = request_payload_dict(request)
        dispatch_result = dispatch_report_llm_reimport(
            report_id=pdf_id,
            payload=payload,
        )
        response_payload: dict[str, Any] = {
            **dispatch_result.to_dict(),
            "pdf_id": pdf_id,
            "pdf_hash": pdf_hash,
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

    def get(self, request: Request, pk: int, job_id: str) -> Response:
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
