from django.shortcuts import render
from django.contrib.admin.views.decorators import staff_member_required

@staff_member_required  # Ensures only staff members can access the page
def start_examination(request):
    return render(request, 'admin/start_examination.html')  # Loads the simple HTML page

from django.shortcuts import render
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
from django.shortcuts import render
from rest_framework import viewsets
from ..models import Patient
from ..serializers import PatientSerializer
from rest_framework.permissions import IsAuthenticatedOrReadOnly

class PatientViewSet(viewsets.ModelViewSet):
    """API endpoint for managing patients."""
    queryset = Patient.objects.all()
    serializer_class = PatientSerializer
    permission_classes = [IsAuthenticatedOrReadOnly] 

    def perform_create(self, serializer):
        serializer.save()

from django.http import JsonResponse
from ..models import FindingLocationClassification, FindingLocationClassificationChoice

def get_location_choices(request, location_id):
    """
    Fetch location choices dynamically based on the selected FindingLocationClassification (Location).
    """
    try:
        location = FindingLocationClassification.objects.get(id=location_id)
        location_choices = location.choices.all()  # Get choices via Many-to-Many relationship
        data = [{"id": choice.id, "name": choice.name} for choice in location_choices]
    except FindingLocationClassification.DoesNotExist:
        data = []

    return JsonResponse({"location_choices": data})

from django.http import JsonResponse
from ..models import FindingMorphologyClassification, FindingMorphologyClassificationChoice, FindingMorphologyClassificationType
from django.core.exceptions import ObjectDoesNotExist



def get_morphology_choices(request, morphology_id):
    """
    Fetch morphology choices dynamically based on the selected FindingMorphologyClassification.
    """
    try:
        # Find the selected Morphology Classification
        morphology_classification = FindingMorphologyClassification.objects.get(id=morphology_id)

        # Fetch choices from FindingMorphologyClassificationType using classification_type_id
        morphology_choices = FindingMorphologyClassificationType.objects.filter(
            id=morphology_classification.classification_type_id
        )

        #  Cpnvert QuerySet to JSON
        data = [{"id": choice.id, "name": choice.name} for choice in morphology_choices]

        return JsonResponse({"morphology_choices": data})  #  Always return JSON

    except ObjectDoesNotExist:
        return JsonResponse({"error": "Morphology classification not found", "morphology_choices": []}, status=404)

    except Exception as e:
        print(f"Error fetching morphology choices: {e}")  # Debugging Log
        return JsonResponse({"error": "Internal server error", "morphology_choices": []}, status=500)

