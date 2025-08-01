from endoreg_db.serializers.patient_finding.patient_finding import PatientFindingSerializer
from rest_framework.viewsets import ModelViewSet
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework import status
from endoreg_db.models.medical.patient.patient_finding import PatientFinding
from endoreg_db.models.medical.patient.patient_finding_location import PatientFindingLocation
from endoreg_db.models.medical.patient.patient_finding_morphology import PatientFindingMorphology
from endoreg_db.models import ( FindingLocationClassificationChoice, FindingMorphologyClassificationChoice )
from rest_framework import serializers
from utils.permissions import EnvironmentAwarePermission

class PatientFindingViewSet(ModelViewSet):
    queryset = PatientFinding.objects.all()
    serializer_class = PatientFindingSerializer

@api_view(['POST'])
@permission_classes([EnvironmentAwarePermission])
def create_patient_finding_location(request):
    """
    Create a patient finding location relationship.
    Expected payload: {
        "patient_finding_id": 1,
        "location_classification_choice_id": 2
    }
    """
    try:
        patient_finding_id = request.data.get('patient_finding_id')
        choice_id = request.data.get('location_classification_choice_id')
        
        if not patient_finding_id or not choice_id:
            return Response(
                {'detail': 'patient_finding_id and location_classification_choice_id are required'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get the objects
        try:
            patient_finding = PatientFinding.objects.get(id=patient_finding_id)
        except PatientFinding.DoesNotExist:
            return Response(
                {'detail': f'PatientFinding with id {patient_finding_id} not found'}, 
                status=status.HTTP_404_NOT_FOUND
            )
        
        try:
            choice = FindingLocationClassificationChoice.objects.get(id=choice_id)
        except FindingLocationClassificationChoice.DoesNotExist:
            return Response(
                {'detail': f'LocationClassificationChoice with id {choice_id} not found'}, 
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Create the relationship
        patient_finding_location = PatientFindingLocation.objects.create(
            patient_finding=patient_finding,
            location_classification_choice=choice
        )
        
        return Response({
            'id': patient_finding_location.id,
            'patient_finding_id': patient_finding.id,
            'location_classification_choice_id': choice.id
        }, status=status.HTTP_201_CREATED)
        
    except Exception as e:
        return Response(
            {'detail': f'Error creating patient finding location: {str(e)}'}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )