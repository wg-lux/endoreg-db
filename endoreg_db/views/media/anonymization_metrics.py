from __future__ import annotations

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.services.anonymization_metrics import (
    build_anonymization_metrics_payload,
    parse_metrics_filters,
)
from endoreg_db.utils.permissions import EnvironmentAwarePermission


class AnonymizationMetricsView(APIView):
    """
    Derived-only anonymization quality and workflow metrics.

    GET /api/media/anonymization/metrics/
    """

    permission_classes = [EnvironmentAwarePermission, PolicyPermission]

    def get(self, request):
        try:
            filters = parse_metrics_filters(request.query_params)
        except ValueError as exc:
            return Response(
                {"error": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            build_anonymization_metrics_payload(filters),
            status=status.HTTP_200_OK,
        )
