# endoreg_db/views/finding_classification/base.py
from rest_framework.viewsets import ReadOnlyModelViewSet
from rest_framework.permissions import IsAuthenticatedOrReadOnly
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import status
from endoreg_db.models import (
    FindingClassification,
)
from ...serializers.finding_classification import (
    FindingClassificationSerializer,
    FindingClassificationChoiceSerializer,
)

class FindingClassificationViewSet(ReadOnlyModelViewSet):
    queryset = FindingClassification.objects.all()
    serializer_class = FindingClassificationSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    @action(detail=True, methods=['get'])
    def choices(self, request, pk=None):
        """
        Get choices for a specific location classification.
        Called by: GET /api/finding-classifications/{id}/choices/
        """
        try:
            classification = self.get_object()
            choices = classification.get_choices()
            
            serializer = FindingClassificationChoiceSerializer(choices, many=True)
            return Response(serializer.data)
        except FindingClassification.DoesNotExist:
            return Response(
                {"error": "FindingClassification not found"},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

