from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from endoreg_db.models import Examination
from ...serializers.optimized_examination_serializers import FindingSerializer as OptimizedFindingSerializer
from .utils import build_multilingual_response

@api_view(["GET"])
def get_findings_for_exam(request, exam_id):
    exam = get_object_or_404(Examination, id=exam_id)
    findings = exam.get_available_findings()
    serializer = OptimizedFindingSerializer(findings, many=True)
    return Response(serializer.data)

@api_view(["GET"])
def get_findings_for_examination(request, examination_id):
    exam = get_object_or_404(Examination, id=examination_id)
    findings = exam.get_available_findings()
    return Response([
        build_multilingual_response(f)
        for f in findings
    ])
