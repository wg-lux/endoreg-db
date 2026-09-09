from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.services.study_cohort import (
    build_study_cohort_payload,
    parse_study_cohort_filters,
)
from endoreg_db.utils.permissions import EnvironmentAwarePermission


class StudyCohortPreviewView(APIView):
    """Read-only, pseudonymous report/video cohort preview for register studies."""

    permission_classes = [EnvironmentAwarePermission, PolicyPermission]

    def get(self, request: Request) -> Response:
        query_params = cast(object, request.query_params)
        filters_payload: Mapping[str, object] = (
            cast(Mapping[str, object], query_params)
            if isinstance(query_params, Mapping)
            else {}
        )
        try:
            filters = parse_study_cohort_filters(filters_payload)
        except ValueError as exc:
            return Response(
                {"error": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            build_study_cohort_payload(filters, request=request),
            status=status.HTTP_200_OK,
        )
