from django.shortcuts import render
from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.core.exceptions import ObjectDoesNotExist
from rest_framework import viewsets
from ..models import Patient
from ..serializers import PatientSerializer
from rest_framework.permissions import IsAuthenticatedOrReadOnly
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
        
    except FindingLocationClassification.DoesNotExist:
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
        serializer.save()
    
    def update(self, request, *args, **kwargs):
        # custom edit logic here if needed
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        # custom delete logic here if needed
        return super().destroy(request, *args, **kwargs)

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
