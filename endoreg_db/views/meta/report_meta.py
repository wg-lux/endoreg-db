from endoreg_db.models import RawPdfFile

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

class ReportFileMetadataView(APIView):
    """
    API-Endpunkt für Report-Datei-Metadaten
    GET /api/reports/{report_id}/file-metadata/
    """

    def get(self, _request, report_id):
        try:
            report = get_object_or_404(RawPdfFile, id=report_id)

            if not report.file:
                return Response(
                    {"error": "Keine Datei für diesen Report verfügbar"},
                    status=status.HTTP_404_NOT_FOUND
                )

            metadata = self._get_file_metadata(report)
            return Response(metadata)

        except (ValueError, TypeError) as e:
            logger.error("Fehler beim Laden der Datei-Metadaten: %s", str(e))
            return Response(
                {"error": "Metadaten konnten nicht geladen werden"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def _get_file_metadata(self, report):
        """Sammelt Datei-Metadaten"""
        file_path = Path(report.file.name)

        try:
            file_size = report.file.size
        except OSError:
            file_size = 0

        return {
            'filename': file_path.name,
            'file_type': file_path.suffix.lower().lstrip('.'),
            'file_size': file_size,
            'upload_date': report.created_at if hasattr(report, 'created_at') else None,
            'last_modified': report.updated_at if hasattr(report, 'updated_at') else None
        }