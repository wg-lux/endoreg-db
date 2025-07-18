from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from endoreg_db.models import Examination, FindingClassification, Finding
from .utils import build_multilingual_response

@api_view(["GET"])
def get_location_classifications_for_exam(request, exam_id):
    exam = get_object_or_404(Examination, id=exam_id)
    location_classifications = FindingClassification.objects.filter(
        classification_types__name__iexact="location",
        examinations=exam
    )
    return Response([{"id": lc.id, "name": lc.name} for lc in location_classifications])

@api_view(["GET"])
def get_morphology_classifications_for_exam(request, exam_id):
    exam = get_object_or_404(Examination, id=exam_id)
    morphology_classifications = FindingClassification.objects.filter(
        classification_types__name__iexact="morphology",
        examinations=exam
    )
    return Response([{"id": mc.id, "name": mc.name} for mc in morphology_classifications])

@api_view(["GET"])
def get_location_choices_for_classification(request, exam_id, location_classification_id):
    exam = get_object_or_404(Examination, id=exam_id)
    location_classification = get_object_or_404(
        FindingClassification,
        id=location_classification_id,
        classification_types__name__iexact="location",
        examinations=exam
    )
    choices = location_classification.get_choices()
    return Response([
        {"id": c.id, "name": c.name, "classificationId": location_classification_id}
        for c in choices
    ])

@api_view(["GET"])
def get_morphology_choices_for_classification(request, exam_id, morphology_classification_id):
    exam = get_object_or_404(Examination, id=exam_id)
    morphology_classification = get_object_or_404(
        FindingClassification,
        id=morphology_classification_id,
        classification_types__name__iexact="morphology",
        examinations=exam
    )
    choices = morphology_classification.get_choices()
    return Response([
        {"id": c.id, "name": c.name, "classificationId": morphology_classification_id}
        for c in choices
    ])

@api_view(["GET"])
def get_location_classifications_for_finding(request, finding_id):
    finding = get_object_or_404(Finding, id=finding_id)
    location_classifications = FindingClassification.objects.filter(
        classification_types__name__iexact="location",
        findings=finding
    )
    result = [
        build_multilingual_response(lc, include_choices=True)
        for lc in location_classifications
    ]
    return Response(result)

@api_view(["GET"])
def get_morphology_classifications_for_finding(request, finding_id):
    finding = get_object_or_404(Finding, id=finding_id)
    morphology_classifications = FindingClassification.objects.filter(
        classification_types__name__iexact="morphology",
        findings=finding
    )
    result = [
        build_multilingual_response(mc, include_choices=True)
        for mc in morphology_classifications
    ]
    return Response(result)

@api_view(["GET"])
def get_choices_for_location_classification(request, classification_id):
    classification = get_object_or_404(FindingClassification, id=classification_id, classification_types__name__iexact="location")
    choices = classification.get_choices()
    result = [
        build_multilingual_response(choice, classification_id=classification.id)
        for choice in choices
    ]
    return Response(result)

@api_view(["GET"])
def get_choices_for_morphology_classification(request, classification_id):
    classification = get_object_or_404(FindingClassification, id=classification_id, classification_types__name__iexact="morphology")
    choices = classification.get_choices()
    result = [
        build_multilingual_response(choice, classification_id=classification.id)
        for choice in choices
    ]
    return Response(result)
