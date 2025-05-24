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
    FindingLocationClassificationChoice, FindingIntervention #, Instrument
)
from django.shortcuts import get_object_or_404

class ExaminationViewSet(ReadOnlyModelViewSet):
    queryset = Examination.objects.all()
    serializer_class = ExaminationSerializer

@api_view(["GET"])
def get_morphology_classification_choices_for_exam(request, exam_id):
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
    exam = get_object_or_404(Examination, id=exam_id)
    findings = exam.get_available_findings()
    interventions = FindingIntervention.objects.filter(findings__in=findings).distinct()

    return Response([{"id": i.id, "name": i.name} for i in interventions])


@api_view(["GET"])
def get_instruments_for_exam(request, exam_id):
    # Placeholder if you plan to link instruments to exams
    return Response([])
