from endoreg_db.models import Examination, FindingIntervention


from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view
from rest_framework.response import Response


@api_view(["GET"])
def get_interventions_for_examination(request, exam_id):
    exam = get_object_or_404(Examination, id=exam_id)
    findings = exam.get_available_findings()
    interventions = FindingIntervention.objects.filter(findings__in=findings).distinct()
    return Response([{"id": i.id, "name": i.name} for i in interventions])