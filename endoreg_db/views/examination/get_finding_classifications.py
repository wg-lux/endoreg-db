
from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from endoreg_db.models import Examination, FindingClassification
from endoreg_db.utils.translation import build_multilingual_response

@api_view(["GET"])
def get_classifications_for_examination(request, exam_id):
    """
    Retrieves all classifications for a specific examination.
    
    Returns a list of all FindingClassification objects linked to the given examination.
    """
    exam = get_object_or_404(Examination, id=exam_id)
    classifications = FindingClassification.objects.filter(examinations=exam)
    result = [build_multilingual_response(c) for c in classifications]
    return Response(result)

@api_view(["GET"])
def get_location_classifications_for_examination(request, exam_id):
    exam = get_object_or_404(Examination, id=exam_id)
    location_classifications = FindingClassification.objects.filter(
        classification_types__name__iexact="location",
        examinations=exam
    )
    return Response([{"id": lc.id, "name": lc.name} for lc in location_classifications])

@api_view(["GET"])
def get_morphology_classifications_for_examination(request, exam_id):
    exam = get_object_or_404(Examination, id=exam_id)
    morphology_classifications = FindingClassification.objects.filter(
        classification_types__name__iexact="morphology",
        examinations=exam
    )
    return Response([{"id": mc.id, "name": mc.name} for mc in morphology_classifications])