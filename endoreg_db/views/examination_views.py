# endoreg_db/views/examination_views.py
from rest_framework.viewsets import ReadOnlyModelViewSet
from endoreg_db.models import Examination
from ..serializers.examination import ExaminationSerializer

# views/examination_views.py
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from endoreg_db.models import (
    Examination, FindingMorphologyClassificationChoice,
    FindingMorphologyClassification, Finding,
    FindingLocationClassificationChoice, FindingIntervention #, Instrument
)
from django.shortcuts import get_object_or_404

class ExaminationViewSet(ReadOnlyModelViewSet):
    queryset = Examination.objects.all()
    serializer_class = ExaminationSerializer

@api_view(["GET"])
def get_morphology_classification_choices_for_exam(request, exam_id):
        """
        Retrieves distinct morphology classification choices available for a given examination.
        
        Returns a list of morphology classification choices associated with the required and optional morphology classification types of the findings available in the specified examination. Each choice includes its ID and name.
        """
        exam = get_object_or_404(Examination, id=exam_id)
        findings = exam.get_available_findings()
    
        choices = FindingMorphologyClassificationChoice.objects.filter(
            classification__in=FindingMorphologyClassification.objects.filter(
                classification_type__in=[
                    *[f for finding in findings for f in finding.required_morphology_classification_types.all()],
                    *[f for finding in findings for f in finding.optional_morphology_classification_types.all()]
                ]
            )
        ).distinct()
    
        return Response([{"id": c.id, "name": c.name} for c in choices])
    
    
@api_view(["GET"])
def get_location_classification_choices_for_exam(request, exam_id):
    """
    Retrieves distinct location classification choices available for a given examination.
    
    Returns a JSON response containing a list of unique location classification choices
    associated with the findings available in the specified examination. Each choice
    includes its `id` and `name`.
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
    Retrieves distinct interventions associated with the findings of a specific examination.
    
    Returns a JSON response containing a list of interventions, each with its ID and name, linked to the findings available in the examination identified by `exam_id`.
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
    
    This is a placeholder endpoint for future implementation of instrument retrieval linked to an examination.
    """
    return Response([])
