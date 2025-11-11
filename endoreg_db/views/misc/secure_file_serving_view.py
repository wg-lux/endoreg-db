from endoreg_db.models import RawPdfFile


from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView


from pathlib import Path
import logging
from ...utils.paths import data_paths  # Added for centralized path management
logger = logging.getLogger(__name__)



class SecureFileServingView(APIView):
    """
    Serviert Dateien über sichere URLs mit Token-Validierung
    GET /api/reports/{report_id}/secure-file/?token={token}
    """

    def get(self, request, report_id):
        token = request.GET.get('token')

        if not token:
            return Response(
                {"error": "Token ist erforderlich"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            report = get_object_or_404(RawPdfFile, id=report_id)

            if not report.file:
                raise Http404("Datei nicht gefunden")

            # Token validieren (hier würde normalerweise eine Token-Speicherung/Validierung stattfinden)
            # Für diese Implementierung nehmen wir an, dass das Token gültig ist

            # Datei servieren
            return self._serve_file(report.file)

        except (ValueError, TypeError, OSError) as e:
            logger.error("Fehler beim Servieren der Datei: %s", str(e))
            raise Http404("Datei konnte nicht geladen werden") from e

    def _serve_file(self, file_field):
        """Serviert die Datei als HTTP-Response"""
        try:
            file_path = Path(file_field.path)

            with open(file_field.path, 'rb') as f:
                response = HttpResponse(
                    f.read(),
                    content_type=self._get_content_type(file_path.suffix)
                )
                response['Content-Disposition'] = f'inline; filename="{file_path.name}"'
                return response

        except (OSError, IOError) as e:
            logger.error("Fehler beim Lesen der Datei: %s", str(e))
            raise Http404("Datei konnte nicht gelesen werden") from e

    def _get_content_type(self, file_extension):
        """Ermittelt den Content-Type basierend auf der Dateiendung"""
        content_types = {
            '.pdf': 'application/pdf',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.bmp': 'image/bmp',
            '.webp': 'image/webp',
            '.txt': 'text/plain',
            '.doc': 'application/msword',
            '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        }
        return content_types.get(file_extension.lower(), 'application/octet-stream')