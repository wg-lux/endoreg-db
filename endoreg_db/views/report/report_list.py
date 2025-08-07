from endoreg_db.models import RawPdfFile
from endoreg_db.serializers.report.report_list import ReportListSerializer

from django.core.paginator import Paginator
from django.db.models import Q
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

import logging
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