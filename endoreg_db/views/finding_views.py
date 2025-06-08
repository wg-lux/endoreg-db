# endoreg_db/views/finding_views.py
from rest_framework.viewsets import ReadOnlyModelViewSet
from rest_framework.permissions import IsAuthenticatedOrReadOnly
from rest_framework import serializers
from ..models import Finding

class FindingSerializer(serializers.ModelSerializer):
    optionalLocationClassifications = serializers.SerializerMethodField()
    requiredLocationClassifications = serializers.SerializerMethodField()
    optionalMorphologyClassifications = serializers.SerializerMethodField()
    requiredMorphologyClassifications = serializers.SerializerMethodField()

    class Meta:
        model = Finding
        fields = [
            'id', 'name', 'name_de', 'name_en', 'description', 'description_de', 'description_en',
            'optionalLocationClassifications', 'requiredLocationClassifications',
            'optionalMorphologyClassifications', 'requiredMorphologyClassifications'
        ]

    def get_optionalLocationClassifications(self, obj):
        # Return location classifications that are optional for this finding
        location_classifications = obj.location_classifications.all()
        return [
            {
                'id': lc.id,
                'name': lc.name,
                'name_de': getattr(lc, 'name_de', ''),
                'name_en': getattr(lc, 'name_en', ''),
                'description': getattr(lc, 'description', ''),
                'description_de': getattr(lc, 'description_de', ''),
                'description_en': getattr(lc, 'description_en', ''),
                'choices': [
                    {
                        'id': choice.id,
                        'name': choice.name,
                        'name_de': getattr(choice, 'name_de', ''),
                        'name_en': getattr(choice, 'name_en', ''),
                        'description': getattr(choice, 'description', ''),
                        'description_de': getattr(choice, 'description_de', ''),
                        'description_en': getattr(choice, 'description_en', ''),
                        'classificationId': choice.location_classification.id if hasattr(choice, 'location_classification') else lc.id,
                        'subcategories': getattr(choice, 'subcategories', {}),
                        'numerical_descriptors': getattr(choice, 'numerical_descriptors', {})
                    }
                    for choice in lc.choices.all()
                ]
            }
            for lc in location_classifications
        ]

    def get_requiredLocationClassifications(self, obj):
        # For now, return empty - implement based on your business logic
        return []

    def get_optionalMorphologyClassifications(self, obj):
        # Return morphology classifications that are optional for this finding
        morphology_classifications = obj.morphology_classifications.all()
        return [
            {
                'id': mc.id,
                'name': mc.name,
                'name_de': getattr(mc, 'name_de', ''),
                'name_en': getattr(mc, 'name_en', ''),
                'description': getattr(mc, 'description', ''),
                'description_de': getattr(mc, 'description_de', ''),
                'description_en': getattr(mc, 'description_en', ''),
                'choices': [
                    {
                        'id': choice.id,
                        'name': choice.name,
                        'name_de': getattr(choice, 'name_de', ''),
                        'name_en': getattr(choice, 'name_en', ''),
                        'description': getattr(choice, 'description', ''),
                        'description_de': getattr(choice, 'description_de', ''),
                        'description_en': getattr(choice, 'description_en', ''),
                        'classificationId': choice.morphology_classification.id if hasattr(choice, 'morphology_classification') else mc.id,
                        'subcategories': getattr(choice, 'subcategories', {}),
                        'numerical_descriptors': getattr(choice, 'numerical_descriptors', {})
                    }
                    for choice in mc.choices.all()
                ]
            }
            for mc in morphology_classifications
        ]

    def get_requiredMorphologyClassifications(self, obj):
        # For now, return empty - implement based on your business logic
        return []

class FindingViewSet(ReadOnlyModelViewSet):
    queryset = Finding.objects.all()
    serializer_class = FindingSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]