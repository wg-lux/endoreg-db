# endoreg_db/views/classification_views.py
from rest_framework.viewsets import ReadOnlyModelViewSet
from rest_framework.permissions import IsAuthenticatedOrReadOnly
from rest_framework.decorators import action
from rest_framework.response import Response
from endoreg_db.models import (
    FindingLocationClassification,
    FindingMorphologyClassification,
)
from ..serializers.optimized_examination_serializers import (
    FindingLocationClassificationSerializer,
    FindingMorphologyClassificationSerializer,
)

class LocationClassificationViewSet(ReadOnlyModelViewSet):
    queryset = FindingLocationClassification.objects.all()
    serializer_class = FindingLocationClassificationSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    @action(detail=True, methods=['get'])
    def choices(self, request, pk=None):
        """
        Get choices for a specific location classification.
        Called by: GET /api/location-classifications/{id}/choices/
        """
        try:
            classification = self.get_object()
            choices = classification.get_choices()
            
            result = []
            for choice in choices:
                result.append({
                    'id': choice.id,
                    'name': choice.name,
                    'name_de': getattr(choice, 'name_de', ''),
                    'name_en': getattr(choice, 'name_en', ''),
                    'description': getattr(choice, 'description', ''),
                    'description_de': getattr(choice, 'description_de', ''),
                    'description_en': getattr(choice, 'description_en', ''),
                    'classificationId': classification.id
                })
            
            return Response(result)
        except Exception as e:
            return Response({'error': str(e)}, status=500)

class MorphologyClassificationViewSet(ReadOnlyModelViewSet):
    queryset = FindingMorphologyClassification.objects.all()
    serializer_class = FindingMorphologyClassificationSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    @action(detail=True, methods=['get'])
    def choices(self, request, pk=None):
        """
        Get choices for a specific morphology classification.
        Called by: GET /api/morphology-classifications/{id}/choices/
        """
        try:
            classification = self.get_object()
            choices = classification.get_choices()
            
            result = []
            for choice in choices:
                result.append({
                    'id': choice.id,
                    'name': choice.name,
                    'name_de': getattr(choice, 'name_de', ''),
                    'name_en': getattr(choice, 'name_en', ''),
                    'description': getattr(choice, 'description', ''),
                    'description_de': getattr(choice, 'description_de', ''),
                    'description_en': getattr(choice, 'description_en', ''),
                    'classificationId': classification.id
                })
            
            return Response(result)
        except Exception as e:
            return Response({'error': str(e)}, status=500)