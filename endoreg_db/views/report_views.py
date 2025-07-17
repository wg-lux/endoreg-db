from django.shortcuts import render
from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.db import transaction
from django.core.exceptions import ProtectedError, ObjectDoesNotExist

from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticatedOrReadOnly

from ..models import Patient
from ..serializers import PatientSerializer
from endoreg_db.models import (
    FindingClassification, FindingClassificationChoice
)


@staff_member_required
def start_examination(request):
    return render(request, 'admin/start_examination.html')


class PatientViewSet(viewsets.ModelViewSet):
    """API endpoint for managing patients."""
    queryset = Patient.objects.all()
    serializer_class = PatientSerializer
    permission_classes = [IsAuthenticatedOrReadOnly] 

    def perform_create(self, serializer):
        serializer.save()

    def destroy(self, request, *args, **kwargs):
        """
        Delete a patient with proper error handling and cascade protection.
        """
        patient = self.get_object()
        
        try:
            with transaction.atomic():
                # Check if patient has related examinations
                examination_count = patient.patient_examinations.count()
                finding_count = sum(exam.patient_findings.count() for exam in patient.patient_examinations.all())
                
                if examination_count > 0:
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
                
        except ProtectedError as e:
            return Response({
                'error': 'Patient deletion failed',
                'reason': 'Patient has protected related objects.',
                'detail': str(e)
            }, status=status.HTTP_409_CONFLICT)
        except Exception as e:
            return Response({
                'error': 'Unexpected error during patient deletion',
                'detail': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['get'])
    def check_deletion_safety(self, request, pk=None):
        """
        Check if a patient can be safely deleted.
        Returns information about related objects.
        """
        patient = self.get_object()
        
        examination_count = patient.patient_examinations.count()
        examinations = patient.patient_examinations.all()
        
        finding_count = sum(exam.patient_findings.count() for exam in examinations)
        video_count = sum(1 for exam in examinations if hasattr(exam, 'video') and exam.video)
        report_count = sum(exam.raw_pdf_files.count() for exam in examinations if hasattr(exam, 'raw_pdf_files'))
        
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


def get_location_choices(request, location_id):
    """
    Fetch location choices dynamically based on the selected FindingLocationClassification (Location).
    """
    try:
        location = FindingClassification.objects.get(id=location_id)
        location_choices = location.choices.all()
        data = [{"id": choice.id, "name": choice.name} for choice in location_choices]
    except FindingClassification.DoesNotExist:
        data = []

    return JsonResponse({"location_choices": data})


def get_morphology_choices(request, morphology_id):
    """
    Fetch morphology choices dynamically based on the selected FindingMorphologyClassification.
    """
    try:
        # Find the selected Morphology Classification
        morphology_classification = FindingClassification.objects.get(id=morphology_id)

        # Fetch choices from FindingClassificationType using classification_type_id
        morphology_choices = FindingClassification.objects.filter(
            id=morphology_classification.classification_type_id
        )

        # Convert QuerySet to JSON
        data = [{"id": choice.id, "name": choice.name} for choice in morphology_choices]

        return JsonResponse({"morphology_choices": data})

    except ObjectDoesNotExist:
        return JsonResponse({"error": "Morphology classification not found", "morphology_choices": []}, status=404)

    except Exception as e:
        print(f"Error fetching morphology choices: {e}")
        return JsonResponse({"error": "Internal server error", "morphology_choices": []}, status=500)

