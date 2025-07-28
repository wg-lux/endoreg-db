from endoreg_db.models import (
    Examination,
    FindingClassificationChoice,
)
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view

@api_view(["GET"])
def get_classification_choices_for_examination(request, exam_id):
    """
    Retrieves distinct morphology classification choices for a specific examination.
    
    Returns a list of unique morphology classification choices associated with the required and optional morphology classification types of findings linked to the given examination.
    """
    exam = get_object_or_404(Examination, id=exam_id)
    findings = exam.get_available_findings()
    all_classifications = set()
    for finding in findings:
        all_classifications.update(set(finding.get_classifications()))
    choices = FindingClassificationChoice.objects.filter(
        classification__in=all_classifications
    ).distinct()
    return Response([{"id": c.id, "name": c.name} for c in choices])

@api_view(["GET"])
def get_morphology_classification_choices_for_examination(request, exam_id):
    """
    Retrieves distinct morphology classification choices for a specific examination.
    
    Returns a list of unique morphology classification choices associated with the required and optional morphology classification types of findings linked to the given examination.
    """
    exam = get_object_or_404(Examination, id=exam_id)
    findings = exam.get_available_findings()
    all_classifications = set()
    for finding in findings:
        all_classifications.update(set(finding.get_morphology_classifications()))
    choices = FindingClassificationChoice.objects.filter(
        classification__in=all_classifications
    ).distinct()
    return Response([{"id": c.id, "name": c.name} for c in choices])

@api_view(["GET"])
def get_location_classification_choices_for_examination(request, exam_id):
    """
    Retrieves distinct location classification choices for a specific examination.
    
    Returns a list of unique location classification choices associated with the required and optional location classification types of findings linked to the given examination.
    """
    
    exam = get_object_or_404(Examination, id=exam_id)
    findings = exam.get_available_findings()
    all_classifications = set()
    for finding in findings:
        all_classifications.update(set(finding.get_location_classifications()))
    choices = FindingClassificationChoice.objects.filter(
        classification__in=all_classifications
    ).distinct()
    return Response([{"id": c.id, "name": c.name} for c in choices])