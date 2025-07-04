from django.shortcuts import render
from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction

from rest_framework import viewsets, status, serializers
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticatedOrReadOnly

from ..models import Patient, Gender, Center
from ..serializers.patient import PatientSerializer, GenderSerializer, CenterSerializer
from endoreg_db.models import (
    FindingLocationClassification,
    FindingLocationClassificationChoice,
    FindingMorphologyClassification,
    FindingMorphologyClassificationType
)

@staff_member_required  # Ensures only staff members can access the page
def start_examination(request):
    return render(request, 'admin/start_examination.html')  # Loads the simple HTML page

#from ..models.patient.patient_finding_location import PatientFindingLocation
from ..models import FindingLocationClassification, FindingLocationClassificationChoice  # Correct models


#need to implement one with json data after tesing whethe rthis works or not
"""def get_location_choices(request, location_id):
   
    try:
        # Ensure the location exists
        location = FindingLocationClassification.objects.get(id=location_id)
        # Get only choices related to the selected location classification
        #location_choices = FindingLocationClassificationChoice.objects.filter(location_classification=location)
        #its many to may relation so
        location_choices = location.choices.all()
        
    except ObjectDoesNotExist:
        location_choices = []

    # Get previously selected values to retain them after reloading
    selected_location = int(location_id) if location_id else None

    return render(request, 'admin/patient_finding_intervention.html', {
        "location_choices": location_choices,  # Pass updated choices to the template
        "selected_location": location_id,  # Keep previous selection
    })
"""

class PatientViewSet(viewsets.ModelViewSet):
    """API endpoint for managing patients."""
    queryset = Patient.objects.all()
    serializer_class = PatientSerializer
    #permission_classes = [IsAuthenticatedOrReadOnly] 

    def perform_create(self, serializer):
        """Erweiterte Validierung beim Erstellen eines Patienten"""
        try:
            # Zusätzliche Validierung falls nötig
            patient = serializer.save()
            return patient
        except Exception as e:
            raise serializers.ValidationError(f"Fehler beim Erstellen des Patienten: {str(e)}")
    
    def update(self, request, *args, **kwargs):
        """Erweiterte Logik für das Aktualisieren von Patienten"""
        try:
            return super().update(request, *args, **kwargs)
        except Exception as e:
            return Response(
                {"error": f"Fehler beim Aktualisieren des Patienten: {str(e)}"}, 
                status=status.HTTP_400_BAD_REQUEST
            )

    def destroy(self, request, *args, **kwargs):
        """
        Delete a patient with proper error handling and cascade protection.
        """
        patient = self.get_object()
        
        try:
            with transaction.atomic():
                # Check if patient has related examinations
                examination_count = patient.patient_examinations.count() if hasattr(patient, 'patient_examinations') else 0
                finding_count = 0
                
                if examination_count > 0:
                    finding_count = sum(
                        exam.patient_findings.count() if hasattr(exam, 'patient_findings') else 0 
                        for exam in patient.patient_examinations.all()
                    )
                    
                    return Response({
                        'error': 'Patient cannot be deleted',
                        'reason': f'Patient has {examination_count} examination(s) and {finding_count} finding(s).',
                        'detail': 'Please remove all related examinations and findings before deleting the patient.'
                    }, status=status.HTTP_409_CONFLICT)
                
                # Check if this is a real person (additional protection)
                if hasattr(patient, 'is_real_person') and patient.is_real_person:
                    return Response({
                        'error': 'Cannot delete real patient',
                        'reason': 'This patient is marked as a real person.',
                        'detail': 'Real patient data cannot be deleted for data protection reasons.'
                    }, status=status.HTTP_403_FORBIDDEN)
                
                # Perform the deletion
                patient_name = f"{patient.first_name} {patient.last_name}"
                patient.delete()
                
                return Response({
                    'message': f'Patient "{patient_name}" has been successfully deleted.'
                }, status=status.HTTP_200_OK)
                
        except Exception as e:
            return Response({
                'error': 'Patient deletion failed',
                'reason': 'Patient has protected related objects.',
                'detail': str(e)
            }, status=status.HTTP_409_CONFLICT)
        except Exception as e:
            return Response(
                {"error": f"Fehler beim Löschen des Patienten: {str(e)}"}, 
                status=status.HTTP_400_BAD_REQUEST
            )

    @action(detail=True, methods=['get'])
    def check_deletion_safety(self, request, pk=None):
        """
        Check if a patient can be safely deleted.
        Returns information about related objects.
        """
        patient = self.get_object()
        
        examination_count = patient.patient_examinations.count() if hasattr(patient, 'patient_examinations') else 0
        examinations = patient.patient_examinations.all() if hasattr(patient, 'patient_examinations') else []
        
        finding_count = sum(
            exam.patient_findings.count() if hasattr(exam, 'patient_findings') else 0 
            for exam in examinations
        )
        video_count = sum(
            1 for exam in examinations 
            if hasattr(exam, 'video') and exam.video
        )
        report_count = sum(
            exam.raw_pdf_files.count() if hasattr(exam, 'raw_pdf_files') else 0 
            for exam in examinations
        )
        
        is_real_person = hasattr(patient, 'is_real_person') and patient.is_real_person
        can_delete = examination_count == 0 and not is_real_person
        
        warnings = []
        if is_real_person:
            warnings.append('This patient is marked as a real person')
        if examination_count > 0:
            warnings.append(f'Patient has {examination_count} examination(s)')
        if finding_count > 0:
            warnings.append(f'Patient has {finding_count} finding(s)')
        
        return Response({
            'can_delete': can_delete,
            'is_real_person': is_real_person,
            'related_objects': {
                'examinations': examination_count,
                'findings': finding_count,
                'videos': video_count,
                'reports': report_count
            },
            'warnings': warnings
        })

    @action(detail=False, methods=['get'])
    def patient_count(self, request):
        """Gibt die Anzahl der Patienten zurück"""
        count = Patient.objects.count()
        return Response({"count": count})

class GenderViewSet(viewsets.ReadOnlyModelViewSet):
    """API endpoint für Gender-Optionen (nur lesend)"""
    queryset = Gender.objects.all()
    serializer_class = GenderSerializer
    #permission_classes = [IsAuthenticatedOrReadOnly]

class CenterViewSet(viewsets.ReadOnlyModelViewSet):
    """API endpoint für Center-Optionen (nur lesend)"""
    queryset = Center.objects.all()
    serializer_class = CenterSerializer
    #permission_classes = [IsAuthenticatedOrReadOnly]

@require_GET
def get_location_choices(request, location_id):
    """Fetch location choices dynamically based on FindingLocationClassification."""
    try:
        location = FindingLocationClassification.objects.get(id=location_id)
        location_choices = location.choices.all()
        data = [{"id": choice.id, "name": choice.name} for choice in location_choices]
        return JsonResponse({"location_choices": data})
    except FindingLocationClassification.DoesNotExist:
        return JsonResponse({"error": "Location classification not found", "location_choices": []}, status=404)

@require_GET
def get_morphology_choices(request, morphology_id):
    """Fetch morphology choices dynamically based on FindingMorphologyClassification."""
    try:
        morphology_classification = FindingMorphologyClassification.objects.get(id=morphology_id)
        morphology_choices = FindingMorphologyClassificationType.objects.filter(
            id=morphology_classification.classification_type_id
        )
        data = [{"id": choice.id, "name": choice.name} for choice in morphology_choices]
        return JsonResponse({"morphology_choices": data})
    except ObjectDoesNotExist:
        return JsonResponse({"error": "Morphology classification not found", "morphology_choices": []}, status=404)
    except Exception as e:
        return JsonResponse({"error": "Internal server error", "morphology_choices": []}, status=500)
