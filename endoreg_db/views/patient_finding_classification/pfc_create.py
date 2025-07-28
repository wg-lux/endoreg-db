from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from endoreg_db.models import (
    PatientFinding,
    FindingClassificationChoice,
    FindingClassification
)

@api_view(['POST'])
def create_patient_finding_classification(request):
    """
    Create a patient finding classification relationship.
    Expected payload: {
        "patient_finding_id": 1,
        "classification_id": 2,
        "classification_choice_id": 2
    }
    """
    try:
        patient_finding_id = request.data.get('patient_finding_id')
        choice_id = request.data.get('classification_choice_id')
        classification_id = request.data.get('classification_id')
        
        if not patient_finding_id or not choice_id:
            return Response(
                {'detail': 'patient_finding_id and classification_choice_id are required'}, 
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
            choice = FindingClassificationChoice.objects.get(id=choice_id)
        except FindingClassificationChoice.DoesNotExist:
            return Response(
                {'detail': f'ClassificationChoice with id {choice_id} not found'}, 
                status=status.HTTP_404_NOT_FOUND
            )

        try:
            FindingClassification.objects.get(id=classification_id)
        except FindingClassification.DoesNotExist:
            return Response(
                {'detail': f'Classification with id {classification_id} not found'}, 
                status=status.HTTP_404_NOT_FOUND
            )
        patient_finding_classification = patient_finding.add_classification(classification_id = classification_id, choice_id = choice_id)
        
        return Response({
            'id': patient_finding_classification.id,
            'patient_finding_id': patient_finding.id,
            'classification_choice_id': choice.id
        }, status=status.HTTP_201_CREATED)
        
    except Exception as e:
        return Response(
            {'detail': f'Error creating patient finding classification: {str(e)}'}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )