from rest_framework.viewsets import ReadOnlyModelViewSet
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticatedOrReadOnly

from endoreg_db.models import (
    Examination
)
from endoreg_db.serializers import ExaminationSerializer, FindingSerializer

class ExaminationViewSet(ReadOnlyModelViewSet):
    """
    ViewSet for handling Examination related operations.
    """
    queryset = Examination.objects.all()
    serializer_class = ExaminationSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    # Deprecated: we only use the ExaminationSerializer for now
    # def get_serializer_class(self):
    #     """
    #     Returns the serializer class based on the request.
    #     """
    #     if self.request.query_params.get('optimized', 'false').lower() == 'true':
    #         return OptimizedExaminationSerializer
    #     return ExaminationSerializer

    @action(detail=True, methods=['get'])
    def findings(self, request, pk=None):
        """
        Get findings associated with a specific examination.
        Called by: GET /api/examinations/{id}/findings/
        """
        examination = self.get_object()
        findings = examination.get_available_findings()
        serializer = FindingSerializer(findings, many=True)
        return Response(serializer.data)