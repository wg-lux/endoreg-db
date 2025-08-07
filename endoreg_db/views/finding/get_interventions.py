from endoreg_db.models import Examination, Finding


from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view
from rest_framework.response import Response


@api_view(["GET"])
def get_interventions_for_finding(request, exam_id, finding_id):
    exam = get_object_or_404(Examination, id=exam_id)
    finding = get_object_or_404(Finding, id=finding_id)
    exam_findings = exam.get_available_findings()
    if finding not in exam_findings:
        return Response({"error": "Finding not found for this examination"}, status=404)
    interventions = finding.finding_interventions.all()
    return Response([{"id": i.id, "name": i.name} for i in interventions])