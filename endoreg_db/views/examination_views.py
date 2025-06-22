# endoreg_db/views/examination_views.py
from rest_framework.viewsets import ModelViewSet
from endoreg_db.models import Examination
from ..serializers.optimized_examination_serializers import (
    ExaminationSerializer as OptimizedExaminationSerializer,
    FindingSerializer as OptimizedFindingSerializer,
)

# views/examination_views.py
from rest_framework.decorators import api_view
from rest_framework.response import Response
from endoreg_db.models import (
    FindingMorphologyClassificationChoice,
    FindingMorphologyClassification,
    FindingLocationClassificationChoice, 
    FindingLocationClassification,
    FindingIntervention,
    Finding
)
from django.shortcuts import get_object_or_404

def build_multilingual_response(obj, include_choices=False, classification_id=None):
    """
    Helper to build a multilingual response dict for an object.
    If include_choices is True, adds a 'choices' key with multilingual dicts for each choice.
    If classification_id is given, adds 'classificationId' to each choice.
    """
    data = {
        'id': obj.id,
        'name': getattr(obj, 'name', ''),
        'name_de': getattr(obj, 'name_de', ''),
        'name_en': getattr(obj, 'name_en', ''),
        'description': getattr(obj, 'description', ''),
        'description_de': getattr(obj, 'description_de', ''),
        'description_en': getattr(obj, 'description_en', ''),
    }
    # Add 'required' if present on the object
    if hasattr(obj, 'required'):
        data['required'] = getattr(obj, 'required', True)
    if include_choices:
        data['choices'] = [
            build_multilingual_response(choice, include_choices=False, classification_id=classification_id or obj.id)
            for choice in obj.get_choices()
        ]
        # Add classificationId to each choice
        for choice_dict in data['choices']:
            choice_dict['classificationId'] = classification_id or obj.id
    if classification_id is not None and not include_choices:
        data['classificationId'] = classification_id
    return data

class ExaminationViewSet(ModelViewSet):
    queryset = Examination.objects.all()
    serializer_class = OptimizedExaminationSerializer

# NEW ENDPOINTS FOR RESTRUCTURED FRONTEND

@api_view(["GET"])
def get_location_classifications_for_exam(request, exam_id):
    """
    Retrieves location classifications linked to a specific examination.
    
    Returns a list of dictionaries containing the ID and name of each location classification associated with the examination identified by `exam_id`.
    """
    exam = get_object_or_404(Examination, id=exam_id)
    location_classifications = exam.location_classifications.all()
    return Response([{"id": lc.id, "name": lc.name} for lc in location_classifications])

@api_view(["GET"])
def get_findings_for_exam(request, exam_id):
    """
    Retrieves findings associated with a specific examination.
    
    Returns a list of dictionaries containing the ID and name of each finding linked to the examination identified by `exam_id`.
    """
    exam = get_object_or_404(Examination, id=exam_id)
    findings = exam.get_available_findings()
    serializer = OptimizedFindingSerializer(findings, many=True)
    return Response(serializer.data)

@api_view(["GET"])
def get_location_choices_for_classification(request, exam_id, location_classification_id):
    """
    Retrieves location choices for a given location classification within an examination.
    
    Returns a list of choices, each with its ID, name, and the associated classification ID. Responds with a 404 error if the location classification does not belong to the specified examination.
    """
    exam = get_object_or_404(Examination, id=exam_id)
    location_classification = get_object_or_404(FindingLocationClassification, id=location_classification_id)

    # Validate that the location classification belongs to this examination
    if location_classification not in exam.location_classifications.all():
        return Response(
            {"error": "Location classification not found for this examination"},
            status=404,
        )

    # Get choices for this specific classification
    choices = location_classification.get_choices()
    return Response(
        [
            {"id": c.id, "name": c.name, "classificationId": location_classification_id}
            for c in choices
        ]
    )

@api_view(["GET"])
def get_interventions_for_finding(request, exam_id, finding_id):
    """
    Retrieves interventions linked to a specific finding within an examination.
    
    Returns a JSON list of interventions with their IDs and names. Responds with a 404 error if the finding does not belong to the specified examination.
    """
    exam = get_object_or_404(Examination, id=exam_id)
    finding = get_object_or_404(Finding, id=finding_id)

    # Validate that the finding belongs to this examination
    exam_findings = exam.get_available_findings()
    if finding not in exam_findings:
        return Response({"error": "Finding not found for this examination"}, status=404)

    # Get interventions for this specific finding
    interventions = finding.finding_interventions.all()
    return Response([{"id": i.id, "name": i.name} for i in interventions])

@api_view(["GET"])
def get_examinations_for_video(request, video_id):
    """
    Placeholder endpoint for retrieving examinations linked to a specific video.
    
    Currently returns an empty list, as the relationship between videos and examinations is not yet implemented.
    """
    from ..models import VideoFile
    video = get_object_or_404(VideoFile, id=video_id)
    
    # For now, return empty list since video-examination relationship needs to be established
    # TODO: Implement proper video-examination relationship
    return Response([])

@api_view(["GET"])
def get_findings_for_examination(request, examination_id):
    """
    Retrieves findings associated with a specific examination.
    NEW: This endpoint matches the ExaminationForm.vue API call pattern
    Called by: GET /api/examinations/{examination_id}/findings/
    """
    exam = get_object_or_404(Examination, id=examination_id)
    findings = exam.get_available_findings()
    
    # Return findings with German names and full details
    return Response([
        build_multilingual_response(f)
        for f in findings
    ])

@api_view(["GET"])
def get_location_classifications_for_finding(request, finding_id):
    """
    Retrieves location classifications for a specific finding.
    NEW: This endpoint matches the ExaminationForm.vue API call pattern
    Called by: GET /api/findings/{finding_id}/location-classifications/
    """
    finding = get_object_or_404(Finding, id=finding_id)
    location_classifications = finding.get_location_classifications()
    
    # Return with choices included and required flag
    result = [
        build_multilingual_response(lc, include_choices=True)
        for lc in location_classifications
    ]
    return Response(result)

@api_view(["GET"])
def get_morphology_classifications_for_finding(request, finding_id):
    """
    Retrieves morphology classifications for a specific finding.
    NEW: This endpoint matches the ExaminationForm.vue API call pattern
    Called by: GET /api/findings/{finding_id}/morphology-classifications/
    """
    finding = get_object_or_404(Finding, id=finding_id)
    
    # Get required and optional classification types separately
    required_types = finding.required_morphology_classification_types.all()
    optional_types = finding.optional_morphology_classification_types.all()
    
    from endoreg_db.models import FindingMorphologyClassification
    
    result = []
    # Process required classifications
    for classification_type in required_types:
        classifications = FindingMorphologyClassification.objects.filter(
            classification_type=classification_type
        )
        for mc in classifications:
            mc_data = build_multilingual_response(mc, include_choices=True)
            mc_data['required'] = True
            result.append(mc_data)
    # Process optional classifications
    for classification_type in optional_types:
        classifications = FindingMorphologyClassification.objects.filter(
            classification_type=classification_type
        )
        for mc in classifications:
            if any(existing['id'] == mc.id for existing in result):
                continue
            mc_data = build_multilingual_response(mc, include_choices=True)
            mc_data['required'] = False
            result.append(mc_data)
    return Response(result)

@api_view(["GET"])
def get_choices_for_location_classification(request, classification_id):
    """
    Retrieves choices for a specific location classification.
    NEW: This endpoint matches the ExaminationForm.vue API call pattern
    Called by: GET /api/location-classifications/{classification_id}/choices/
    """
    classification = get_object_or_404(FindingLocationClassification, id=classification_id)
    choices = classification.get_choices()
    
    result = [
        build_multilingual_response(choice, classification_id=classification.id)
        for choice in choices
    ]
    return Response(result)

@api_view(["GET"])
def get_choices_for_morphology_classification(request, classification_id):
    """
    Retrieves choices for a specific morphology classification.
    NEW: This endpoint matches the ExaminationForm.vue API call pattern
    Called by: GET /api/morphology-classifications/{classification_id}/choices/
    """
    classification = get_object_or_404(FindingMorphologyClassification, id=classification_id)
    choices = classification.get_choices()
    
    result = [
        build_multilingual_response(choice, classification_id=classification.id)
        for choice in choices
    ]
    return Response(result)

# EXISTING ENDPOINTS (KEEPING FOR BACKWARD COMPATIBILITY)

@api_view(["GET"])
def get_morphology_classification_choices_for_exam(request, exam_id):
    """
    Retrieves distinct morphology classification choices for a specific examination.
    
    Returns a list of unique morphology classification choices associated with the required and optional morphology classification types of findings linked to the given examination.
    """
    exam = get_object_or_404(Examination, id=exam_id)
    findings = exam.get_available_findings()
    all_classification_types = set()
    for finding in findings:
        all_classification_types.update(finding.required_morphology_classification_types.all())
        all_classification_types.update(finding.optional_morphology_classification_types.all())
    choices = FindingMorphologyClassificationChoice.objects.filter(
        classification__in=FindingMorphologyClassification.objects.filter(
            classification_type__in=list(all_classification_types)
        )
    ).distinct()
    return Response([{"id": c.id, "name": c.name} for c in choices])

@api_view(["GET"])
def get_location_classification_choices_for_exam(request, exam_id):
    """
    Retrieves distinct location classification choices linked to findings of an examination.
    
    Returns a list of dictionaries with the ID and name of each location classification choice associated with the findings of the specified examination.
    """
    exam = get_object_or_404(Examination, id=exam_id)
    findings = exam.get_available_findings()
    choices = FindingLocationClassificationChoice.objects.filter(
        location_classifications__in=[
            lc for finding in findings for lc in finding.get_location_classifications()
        ]
    ).distinct()
    return Response([{"id": c.id, "name": c.name} for c in choices])

@api_view(["GET"])
def get_interventions_for_exam(request, exam_id):
    """
    Retrieves interventions linked to findings of a specific examination.
    
    Returns:
        JSON response with a list of interventions, each containing its ID and name, associated with the findings available for the specified examination.
    """
    exam = get_object_or_404(Examination, id=exam_id)
    findings = exam.get_available_findings()
    interventions = FindingIntervention.objects.filter(findings__in=findings).distinct()
    return Response([{"id": i.id, "name": i.name} for i in interventions])

@api_view(["GET"])
def get_instruments_for_exam(request, exam_id):
    # Placeholder if you plan to link instruments to exams
    """
    Returns an empty list of instruments for the specified examination.
    
    This placeholder endpoint is intended for future implementation of instrument retrieval linked to an examination.
    """
    return Response([])
