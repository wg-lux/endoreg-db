# endoreg_db/views/patient_finding_views.py
from rest_framework.viewsets import ModelViewSet
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticatedOrReadOnly
from rest_framework import serializers, status
from django.shortcuts import get_object_or_404
from ..models import (
    PatientFinding, 
    PatientFindingLocation, 
    PatientFindingMorphology,
    Finding,
    Patient
)

class PatientFindingSerializer(serializers.ModelSerializer):
    class Meta:
        model = PatientFinding
        fields = [
            'id', 'patient_id', 'finding_id', 'examination_id', 
            'timestamp', 'notes', 'date_start', 'date_stop'
        ]

    def create(self, validated_data):
        # Create PatientFinding instance
        patient_finding = PatientFinding.objects.create(**validated_data)
        return patient_finding

class PatientFindingLocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = PatientFindingLocation
        fields = ['id', 'patient_finding_id', 'location_classification_choice_id']

class PatientFindingMorphologySerializer(serializers.ModelSerializer):
    class Meta:
        model = PatientFindingMorphology
        fields = ['id', 'patient_finding_id', 'morphology_classification_choice_id']

class PatientFindingViewSet(ModelViewSet):
    queryset = PatientFinding.objects.all()
    serializer_class = PatientFindingSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    def perform_create(self, serializer):
        # Additional logic for creating patient findings
        patient_finding = serializer.save()
        return patient_finding

@api_view(['POST'])
def create_patient_finding_location(request):
    """
    Create a PatientFindingLocation entry
    """
    serializer = PatientFindingLocationSerializer(data=request.data)
    if serializer.is_valid():
        location = serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

@api_view(['POST'])
def create_patient_finding_morphology(request):
    """
    Create a PatientFindingMorphology entry
    """
    serializer = PatientFindingMorphologySerializer(data=request.data)
    if serializer.is_valid():
        morphology = serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)