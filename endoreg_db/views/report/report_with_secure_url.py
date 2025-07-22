from endoreg_db.models import RawPdfFile
from endoreg_db.serializers.report.report import ReportDataSerializer

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

import logging
logger = logging.getLogger(__name__)

class ReportWithSecureUrlView(APIView):
    """
    API-Endpunkt für Reports mit sicherer URL-Generierung
    GET /api/reports/{report_id}/with-secure-url/
    """

    def get(self, request, report_id):
        try:
            report = get_object_or_404(RawPdfFile, id=report_id)
            serializer = ReportDataSerializer(report, context={'request': request})
            return Response(serializer.data)
        except (ValueError, TypeError) as e:
            logger.error("Fehler beim Laden des Reports %s: %s", report_id, str(e))
            return Response(
                {"error": "Report konnte nicht geladen werden"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )