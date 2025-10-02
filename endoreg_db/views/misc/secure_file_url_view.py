from endoreg_db.models import RawPdfFile
from endoreg_db.serializers.report.secure_file_url import SecureFileUrlSerializer

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView


import uuid
from datetime import timedelta
from pathlib import Path

import logging
from ...utils.paths import data_paths  # Added for centralized path management
logger = logging.getLogger(__name__)

class SecureFileUrlView(APIView):
    """
    API-Endpunkt für sichere URL-Generierung
    POST /api/secure-file-urls/
    """

    def post(self, request):
        report_id = request.data.get('report_id')
        file_type = request.data.get('file_type', 'pdf')

        if not report_id:
            return Response(
                {"error": "report_id ist erforderlich"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            report = get_object_or_404(RawPdfFile, id=report_id)

            if not report.file:
                return Response(
                    {"error": "Keine Datei für diesen Report verfügbar"},
                    status=status.HTTP_404_NOT_FOUND
                )

            # Sichere URL generieren
            secure_url_data = self._generate_secure_url(report, request, file_type)
            serializer = SecureFileUrlSerializer(data=secure_url_data)

            if serializer.is_valid():
                return Response(serializer.data)
            else:
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        except (ValueError, TypeError) as e:
            logger.error("Fehler beim Generieren der sicheren URL: %s", str(e))
            return Response(
                {"error": "Sichere URL konnte nicht generiert werden"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def _generate_secure_url(self, report, request, file_type):
        """Generiert eine sichere URL mit Token"""
        token = str(uuid.uuid4())
        expires_at = timezone.now() + timedelta(hours=2)

        secure_url = request.build_absolute_uri(
            f"/api/reports/{report.id}/secure-file/?token={token}"
        )

        # Dateigröße ermitteln
        file_size = 0
        try:
            if report.file:
                file_size = report.file.size
        except OSError:
            # Datei nicht verfügbar
            file_size = 0

        return {
            'url': secure_url,
            'expires_at': expires_at,
            'file_type': file_type,
            'original_filename': Path(report.file.name).name if report.file else 'unknown',
            'file_size': file_size
        }