import warnings
from endoreg_db.models import FindingClassification


from django.http import JsonResponse
from django.views.decorators.http import require_GET


@require_GET
def get_classification_choices(self, request, classification_id):
    """
    Fetch classification choices dynamically based on the selected FindingClassification.
    """
    try:
        classification = FindingClassification.objects.get(id=classification_id)
        choices = classification.get_choices()
        data = [{"id": choice.id, "name": choice.name} for choice in choices]
        return JsonResponse({"choices": data})
    except FindingClassification.DoesNotExist:
        return JsonResponse({"error": "Classification not found"}, status=404)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@require_GET
def get_location_choices(request, location_id):
    """
    Fetch location choices dynamically based on the selected FindingLocationClassification (Location).
    """
    warnings.warn_explicit(
        "The get_location_choices function is deprecated and will be removed in future versions. "
        "Use the 'get_classification_choices' method instead.",)

    # Fetch location choices using the new method
    return get_classification_choices(request, location_id)


@require_GET
def get_morphology_choices(request, morphology_id):
    """
    Fetch morphology choices dynamically based on the selected FindingMorphologyClassification.
    """
    warnings.warn_explicit(
        "The get_morphology_choices function is deprecated and will be removed in future versions. "
        "Use the 'get_classification_choices' method instead.",)

    # Fetch morphology choices using the new method
    return get_classification_choices(request, morphology_id)