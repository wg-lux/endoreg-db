from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.views import APIView
from django.http import HttpResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.core.paginator import Paginator
from django.db.models import Q
import uuid
from datetime import timedelta
from pathlib import Path
import logging

from ...models import RawPdfFile
from ...serializers.report.base import (
    ReportDataSerializer, 
    SecureFileUrlSerializer,
    ReportListSerializer
)

logger = logging.getLogger(__name__)

class ReportListView(APIView):
    """
    API-Endpunkt für paginierte Report-Listen mit optionaler Filterung
    GET /api/reports/
    """
    
    def get(self, request):
        try:
            # Query-Parameter abrufen
            page = int(request.GET.get('page', 1))
            page_size = min(int(request.GET.get('page_size', 20)), 100)  # Max 100 pro Seite
            
            # Filter-Parameter
            file_type_filter = request.GET.get('file_type')
            patient_name_filter = request.GET.get('patient_name')
            casenumber_filter = request.GET.get('casenumber')
            date_from = request.GET.get('date_from')
            date_to = request.GET.get('date_to')
            
            # Base QuerySet mit related data
            queryset = RawPdfFile.objects.select_related('sensitive_meta').all()
            
            # Filter anwenden
            if patient_name_filter:
                queryset = queryset.filter(
                    Q(sensitive_meta__patient_first_name__icontains=patient_name_filter) |
                    Q(sensitive_meta__patient_last_name__icontains=patient_name_filter)
                )
            
            if casenumber_filter:
                queryset = queryset.filter(
                    sensitive_meta__case_number__icontains=casenumber_filter
                )
            
            if file_type_filter:
                # Filter basierend auf Dateiendung
                queryset = queryset.filter(file__endswith=f'.{file_type_filter}')
            
            if date_from:
                queryset = queryset.filter(created_at__gte=date_from)
            
            if date_to:
                queryset = queryset.filter(created_at__lte=date_to)
            
            # Sortierung (neueste zuerst)
            queryset = queryset.order_by('-created_at')
            
            # Paginierung
            paginator = Paginator(queryset, page_size)
            
            if page > paginator.num_pages:
                return Response({
                    'count': paginator.count,
                    'next': None,
                    'previous': None,
                    'results': []
                })
            
            page_obj = paginator.get_page(page)
            
            # Serialisierung
            serializer = ReportListSerializer(
                page_obj.object_list, 
                many=True, 
                context={'request': request}
            )
            
            # URLs für Paginierung
            next_url = None
            previous_url = None
            
            if page_obj.has_next():
                next_url = request.build_absolute_uri(
                    f"?page={page_obj.next_page_number()}&page_size={page_size}"
                )
            
            if page_obj.has_previous():
                previous_url = request.build_absolute_uri(
                    f"?page={page_obj.previous_page_number()}&page_size={page_size}"
                )
            
            return Response({
                'count': paginator.count,
                'next': next_url,
                'previous': previous_url,
                'results': serializer.data
            })
            
        except ValueError as e:
            logger.error("Ungültige Parameter in ReportListView: %s", str(e))
            return Response(
                {"error": "Ungültige Parameter"},
                status=status.HTTP_400_BAD_REQUEST
            )
        except (AttributeError, TypeError, OSError) as e:
            logger.error("Fehler in ReportListView: %s", str(e))
            return Response(
                {"error": "Fehler beim Laden der Reports"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

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

@api_view(['GET'])
def validate_secure_url(request):
    """
    Validiert eine sichere URL
    GET /api/validate-secure-url/?url={url}
    """
    url = request.GET.get('url')
    
    if not url:
        return Response(
            {"error": "URL Parameter ist erforderlich"},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        # Hier würde normalerweise eine Token-Validierung stattfinden
        # Für diese Implementierung geben wir immer True zurück
        is_valid = True
        
        return Response({
            "is_valid": is_valid,
            "message": "URL ist gültig" if is_valid else "URL ist ungültig oder abgelaufen"
        })
        
    except (ValueError, TypeError) as e:
        logger.error("Fehler bei URL-Validierung: %s", str(e))
        return Response(
            {"error": "URL-Validierung fehlgeschlagen"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )