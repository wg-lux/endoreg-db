# endoreg_db/views/examination_views.py
from rest_framework.viewsets import ReadOnlyModelViewSet
from endoreg_db.models import Examination
from ..serializers.examination import ExaminationSerializer

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

class ExaminationViewSet(ReadOnlyModelViewSet):
    queryset = Examination.objects.all()
    serializer_class = ExaminationSerializer

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
    return Response([{"id": f.id, "name": f.name} for f in findings])

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
