from endoreg_db.models import PatientExamination
from endoreg_db.serializers.patient_examination import PatientExaminationSerializer

from rest_framework import generics, status
from rest_framework.response import Response
from endoreg_db.utils.permissions import DEBUG_PERMISSIONS

import logging
logger = logging.getLogger(__name__)


class PatientExaminationListView(generics.ListAPIView):
    """
    List PatientExamination instances with filtering.
    GET /api/examinations/list/

    Query parameters:
    - patient_id: Filter by patient ID
    - examination_name: Filter by examination name
    - limit: Number of results (default 20)
    - offset: Pagination offset (default 0)
    """
    serializer_class = PatientExaminationSerializer
    permission_classes = DEBUG_PERMISSIONS

    def get_queryset(self):
        queryset = PatientExamination.objects.select_related(
            'patient', 'examination'
        ).order_by('-date_start', '-id')

        # Apply filters
        patient_id = self.request.query_params.get('patient_id')
        if patient_id:
            queryset = queryset.filter(patient_id=patient_id)

        examination_name = self.request.query_params.get('examination_name')
        if examination_name:
            queryset = queryset.filter(examination__name__icontains=examination_name)

        return queryset

    def list(self, request, *args, **kwargs):
        try:
            queryset = self.get_queryset()

            # Pagination
            limit = int(request.query_params.get('limit', 20))
            offset = int(request.query_params.get('offset', 0))

            total_count = queryset.count()
            paginated_queryset = queryset[offset:offset + limit]

            serializer = self.get_serializer(paginated_queryset, many=True)

            return Response({
                'results': serializer.data,
                'total_count': total_count,
                'limit': limit,
                'offset': offset,
                'has_more': (offset + limit) < total_count
            })

        except Exception as e:
            logger.error(f"Error listing examinations: {str(e)}")
            return Response(
                {'error': 'Failed to list examinations'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )