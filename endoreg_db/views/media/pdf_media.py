"""
Report Media Management View (Phase 1.2)

Provides standardized REST API for report files including listing and detail
retrieval for the media management system.
"""

import logging
from datetime import date, datetime
from urllib.parse import urlencode
from collections.abc import Mapping
from typing import TYPE_CHECKING, TypeAlias, cast

from django.db.models import Q
from django.db.models.fields.files import FieldFile
from django.db.models.query import QuerySet
from django.core.exceptions import PermissionDenied
from django.http import Http404
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.utils.api_urls import endoreg_api_path
from endoreg_db.utils.permissions import EnvironmentAwarePermission
from endoreg_db.views.access_control import (
    assert_center_scope_allowed,
    filter_center_scoped_queryset,
)

if TYPE_CHECKING:
    from endoreg_db.models.media.pdf.report_file import AnonymExaminationReport

logger = logging.getLogger(__name__)

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


def _query_params(request: Request) -> Mapping[str, str]:
    return cast(Mapping[str, str], request.query_params)


def _query_str_param(params: Mapping[str, str], key: str, default: str = "") -> str:
    return params.get(key, default)


def _query_int_param(params: Mapping[str, str], key: str, default: int) -> int:
    raw_value = params.get(key)
    if raw_value in ("", None):
        return default
    try:
        return int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer.") from exc


def _safe_get_file_size(file_field: FieldFile | None) -> int:
    if file_field is None or not file_field.name:
        return 0
    try:
        return file_field.size
    except (OSError, ValueError, IOError):
        return 0


def _format_german_date(value: date | datetime | None) -> str | None:
    if value is None:
        return None
    return value.strftime("%d.%m.%Y")


class PdfMediaView(APIView):
    """
    PDF media management API for listing and detail retrieval.
    """

    permission_classes = [EnvironmentAwarePermission, PolicyPermission]

    @staticmethod
    def _resolved_anonymized_text(pdf: RawPdfFile) -> str | None:
        anonymized_text = getattr(pdf, "anonymized_text", None)
        if isinstance(anonymized_text, str) and anonymized_text.strip():
            return anonymized_text

        full_report = cast(
            "AnonymExaminationReport | None",
            getattr(pdf, "anonym_examination_report", None),
        )
        full_report_text = cast(str | None, getattr(full_report, "text", None))
        if full_report_text is not None and full_report_text.strip():
            return full_report_text

        sensitive_meta = cast(
            "SensitiveMeta | None",
            getattr(pdf, "sensitive_meta", None),
        )
        sensitive_text = cast(
            str | None,
            getattr(sensitive_meta, "anonymized_text", None),
        )
        if sensitive_text is not None and sensitive_text.strip():
            return sensitive_text
        return None

    def get(self, request: Request, pk: int | None = None) -> Response:
        if pk is not None:
            return self._get_pdf_detail(request=request, pk=pk)
        return self._list_pdfs(request)

    def _get_pdf_detail(self, *, request: Request, pk: int) -> Response:
        try:
            pdf_id = pk
            pdf = RawPdfFile.objects.select_related(
                "sensitive_meta", "anonym_examination_report"
            ).get(pk=pdf_id)
            assert_center_scope_allowed(request=request, obj=pdf)

            file_obj = cast(FieldFile | None, getattr(pdf, "file", None))
            pdf_hash = cast(str | None, getattr(pdf, "pdf_hash", None))
            date_created = cast(datetime | None, getattr(pdf, "date_created", None))
            resolved_anonymized_text = self._resolved_anonymized_text(pdf)

            pdf_data: dict[str, JsonValue] = {
                "id": cast(int | None, pdf.pk),
                "filename": cast(str | None, getattr(file_obj, "name", None))
                or "Unknown",
                "file_size": _safe_get_file_size(file_obj),
                "pdf_hash": pdf_hash,
                "uploaded_at": (
                    date_created.isoformat()
                    if isinstance(date_created, datetime)
                    else None
                ),
                "anonymized_text": resolved_anonymized_text,
                "has_anonymized_text": bool(resolved_anonymized_text),
                "is_validated": bool(
                    getattr(getattr(pdf, "sensitive_meta", None), "is_verified", False)
                ),
            }

            sensitive_meta = cast(
                SensitiveMeta | None, getattr(pdf, "sensitive_meta", None)
            )
            if sensitive_meta is not None:
                patient_dob = cast(
                    date | datetime | None, getattr(sensitive_meta, "patient_dob", None)
                )
                examination_date = cast(
                    date | datetime | None,
                    getattr(sensitive_meta, "examination_date", None),
                )
                patient_first_name = cast(
                    str | None, getattr(sensitive_meta, "patient_first_name", None)
                )
                patient_last_name = cast(
                    str | None, getattr(sensitive_meta, "patient_last_name", None)
                )
                pdf_data.update(
                    {
                        "patient_first_name": patient_first_name,
                        "patient_last_name": patient_last_name,
                        "patient_dob": _format_german_date(patient_dob),
                        "examination_date": _format_german_date(examination_date),
                    }
                )

            return Response(pdf_data)

        except RawPdfFile.DoesNotExist:
            raise Http404(f"report with ID {pk} not found")
        except (Http404, PermissionDenied):
            raise
        except Exception as exc:
            logger.error(
                f"Unexpected error in report detail view for ID {pk}: {str(exc)}"
            )
            return Response(
                {"error": "Failed to retrieve report details"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _list_pdfs(self, request: Request) -> Response:
        try:
            queryset = RawPdfFile.objects.select_related(
                "sensitive_meta", "anonym_examination_report"
            ).all()
            queryset = cast(
                QuerySet[RawPdfFile],
                filter_center_scoped_queryset(
                    queryset=queryset,
                    user=request.user,
                ),
            )

            query_params = _query_params(request)
            queryset = self._apply_filters(queryset, query_params)

            search = _query_str_param(query_params, "search")
            if search:
                queryset = queryset.filter(Q(file__icontains=search))

            queryset = queryset.order_by("-date_created")

            limit = min(_query_int_param(query_params, "limit", 50), 100)
            offset = _query_int_param(query_params, "offset", 0)

            total_count = queryset.count()
            pdfs = queryset[offset : offset + limit]

            results: list[dict[str, JsonValue]] = []
            for pdf in pdfs:
                file_obj = cast(FieldFile | None, getattr(pdf, "file", None))
                sensitive_meta = cast(
                    SensitiveMeta | None, getattr(pdf, "sensitive_meta", None)
                )
                is_verified = (
                    bool(cast(bool, getattr(sensitive_meta, "is_verified", False)))
                    if sensitive_meta is not None
                    else False
                )
                resolved_anonymized_text = self._resolved_anonymized_text(pdf)

                result: dict[str, JsonValue] = {
                    "id": cast(int | None, pdf.pk),
                    "filename": cast(str | None, getattr(file_obj, "name", None))
                    or "Unknown",
                    "file_size": _safe_get_file_size(file_obj),
                    "pdf_hash": cast(str | None, getattr(pdf, "pdf_hash", None)),
                    "has_anonymized_text": bool(resolved_anonymized_text),
                    "is_validated": is_verified,
                }

                if not resolved_anonymized_text:
                    result["status"] = "not_started"
                elif is_verified:
                    result["status"] = "validated"
                else:
                    result["status"] = "done"
                results.append(result)

            return Response(
                {
                    "count": total_count,
                    "next": self._get_next_url(request, offset, limit, total_count),
                    "previous": self._get_previous_url(request, offset, limit),
                    "results": results,
                }
            )

        except ValueError as exc:
            return Response(
                {"error": f"Invalid query parameter: {str(exc)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as exc:
            logger.error(f"Unexpected error in report list view: {str(exc)}")
            return Response(
                {"error": "Failed to retrieve report list"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _apply_filters(
        self,
        queryset: QuerySet[RawPdfFile],
        query_params: Mapping[str, str],
    ) -> QuerySet[RawPdfFile]:
        status_filter = _query_str_param(query_params, "status").lower()
        patient_examination_filter = _query_str_param(
            query_params, "patient_examination_id"
        )

        if patient_examination_filter:
            try:
                patient_examination_id = int(patient_examination_filter)
            except ValueError as exc:
                raise ValueError("patient_examination_id must be an integer") from exc
            queryset = queryset.filter(examination_id=patient_examination_id)

        if status_filter == "not_started":
            queryset = queryset.filter(
                Q(anonymized_text__isnull=True) | Q(anonymized_text__exact="")
            )
        elif status_filter == "done":
            queryset = queryset.filter(
                ~Q(anonymized_text__isnull=True),
                ~Q(anonymized_text__exact=""),
                Q(sensitive_meta__is_verified=False) | Q(sensitive_meta__isnull=True),
            )
        elif status_filter == "validated":
            queryset = queryset.filter(
                ~Q(anonymized_text__isnull=True),
                ~Q(anonymized_text__exact=""),
                sensitive_meta__is_verified=True,
            )

        return queryset

    def _get_next_url(
        self, request: Request, offset: int, limit: int, total_count: int
    ) -> str | None:
        if offset + limit >= total_count:
            return None
        return self._build_paginated_url(request, offset + limit, limit)

    def _get_previous_url(
        self, request: Request, offset: int, limit: int
    ) -> str | None:
        if offset <= 0:
            return None
        return self._build_paginated_url(request, max(0, offset - limit), limit)

    def _build_paginated_url(self, request: Request, offset: int, limit: int) -> str:
        params: dict[str, str] = dict(_query_params(request))
        params["offset"] = str(offset)
        params["limit"] = str(limit)
        base_url = request.build_absolute_uri(request.path)
        return f"{base_url}?{urlencode(params)}"

    def patch(self, request: Request, pk: int) -> Response:
        return Response(
            {"error": "report metadata updates not yet implemented"},
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )

    def delete(self, request: Request, pk: int) -> Response:
        force_remove_path = endoreg_api_path(f"media-management/force-remove/{pk}/")
        return Response(
            {
                "error": "report deletion not yet implemented",
                "alternative": f"Use DELETE {force_remove_path} instead",
            },
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )
