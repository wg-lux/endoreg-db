# endoreg_db/views/classification_views.py
from rest_framework.viewsets import ReadOnlyModelViewSet
from rest_framework.permissions import IsAuthenticatedOrReadOnly
from rest_framework import serializers
from ..models import FindingLocationClassification, FindingMorphologyClassification

class LocationClassificationSerializer(serializers.ModelSerializer):
    choices = serializers.SerializerMethodField()

    class Meta:
        model = FindingLocationClassification
        fields = [
            'id', 'name', 'name_de', 'name_en', 'description', 'description_de', 'description_en',
            'choices'
        ]

    def get_choices(self, obj):
        return [
            {
                'id': choice.id,
                'name': choice.name,
                'name_de': choice.name_de or '',
                'name_en': choice.name_en or '',
                'description': choice.description or '',
                'description_de': choice.description_de or '',
                'description_en': choice.description_en or '',
                'classificationId': obj.id,
                'subcategories': choice.subcategories or {},
                'numerical_descriptors': choice.numerical_descriptors or {}
            }
            for choice in obj.choices.all()  # This works because choices is ManyToManyField
        ]

class MorphologyClassificationSerializer(serializers.ModelSerializer):
    choices = serializers.SerializerMethodField()

    class Meta:
        model = FindingMorphologyClassification
        fields = [
            # Remove description_de and description_en as they don't exist in the model
            'id', 'name', 'name_de', 'name_en', 'description',
            'choices'
        ]

    def get_choices(self, obj):
        return [
            {
                'id': choice.id,
                'name': choice.name,
                'name_de': choice.name_de or '',
                'name_en': choice.name_en or '',
                'description': choice.description or '',
                'description_de': '',  # Not available in model
                'description_en': '',  # Not available in model
                'classificationId': obj.id,
                'subcategories': choice.subcategories or {},
                'numerical_descriptors': choice.numerical_descriptors or {}
            }
            for choice in obj.choices.all()  # This uses the related_name from ForeignKey
        ]

class LocationClassificationViewSet(ReadOnlyModelViewSet):
    queryset = FindingLocationClassification.objects.all()
    serializer_class = LocationClassificationSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

class MorphologyClassificationViewSet(ReadOnlyModelViewSet):
    queryset = FindingMorphologyClassification.objects.all()
    serializer_class = MorphologyClassificationSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]