# endoreg_db/views/finding_views.py
from rest_framework.viewsets import ReadOnlyModelViewSet
from rest_framework.permissions import IsAuthenticatedOrReadOnly
from rest_framework.decorators import action
from rest_framework.response import Response
from endoreg_db.models import Finding
from ...serializers.finding import FindingSerializer

class FindingViewSet(ReadOnlyModelViewSet):
    queryset = Finding.objects.all()
    serializer_class = FindingSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    @action(detail=True, methods=['get'])
    def location_classifications(self, request, pk=None):
        """
        Get location classifications for a specific finding.
        Called by: GET /api/findings/{id}/location-classifications/
        """
        try:
            finding = self.get_object()
            
            assert isinstance(finding, Finding), "Finding object is not valid"
            location_classifications = finding.get_location_classifications()
            
            # Return with choices included
            result = []
            for lc in location_classifications:
                lc_data = {
                    'id': lc.id,
                    'name': lc.name,
                    'name_de': getattr(lc, 'name_de', ''),
                    'name_en': getattr(lc, 'name_en', ''),
                    'description': getattr(lc, 'description', ''),
                    'description_de': getattr(lc, 'description_de', ''),
                    'description_en': getattr(lc, 'description_en', ''),
                    'required': True,  # TODO: Determine if required or optional
                    'choices': []
                }
                
                # Get choices for this classification
                choices = lc.get_choices()
                for choice in choices:
                    lc_data['choices'].append({
                        'id': choice.id,
                        'name': choice.name,
                        'name_de': getattr(choice, 'name_de', ''),
                        'name_en': getattr(choice, 'name_en', ''),
                        'description': getattr(choice, 'description', ''),
                        'description_de': getattr(choice, 'description_de', ''),
                        'description_en': getattr(choice, 'description_en', ''),
                        'classificationId': lc.id
                    })
                
                result.append(lc_data)
            
            return Response(result)
        except Exception as e:
            return Response({'error': str(e)}, status=500)

    @action(detail=True, methods=['get'])
    def morphology_classifications(self, request, pk=None):
        """
        Get morphology classifications for a specific finding.
        Called by: GET /api/findings/{id}/morphology-classifications/
        """
        try:
            finding = self.get_object()
            assert isinstance(finding, Finding), "Finding object is not valid"
            morphology_classifications = finding.get_morphology_classifications()
            
            # Return with choices included
            result = []
            for mc in morphology_classifications:
                mc_data = {
                    'id': mc.id,
                    'name': mc.name,
                    'name_de': getattr(mc, 'name_de', ''),
                    'name_en': getattr(mc, 'name_en', ''),
                    'description': getattr(mc, 'description', ''),
                    'description_de': getattr(mc, 'description_de', ''),
                    'description_en': getattr(mc, 'description_en', ''),
                    'required': True,  # TODO: Determine if required or optional
                    'choices': []
                }
                
                # Get choices for this classification
                choices = mc.get_choices()
                for choice in choices:
                    mc_data['choices'].append({
                        'id': choice.id,
                        'name': choice.name,
                        'name_de': getattr(choice, 'name_de', ''),
                        'name_en': getattr(choice, 'name_en', ''),
                        'description': getattr(choice, 'description', ''),
                        'description_de': getattr(choice, 'description_de', ''),
                        'description_en': getattr(choice, 'description_en', ''),
                        'classificationId': mc.id
                    })
                
                result.append(mc_data)
            
            return Response(result)
        except Exception as e:
            return Response({'error': str(e)}, status=500)
      
    @action(detail=False, methods=['get'], url_path='by-id/(?P<finding_id>[^/.]+)')
    def get_finding_by_id(self, request, finding_id=None):
        findings_obj = Finding.objects.filter(id=finding_id)
        serializer = FindingSerializer(findings_obj, many=False)
        return Response(serializer.data)
    
