
from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from endoreg_db.models import Examination
from ...serializers.finding import FindingSerializer
from endoreg_db.utils.translation import build_multilingual_response
from endoreg_db.models import PatientExamination

@api_view(["GET"])
def get_findings_for_examination(request, examination_id):
    """
    Retrieve findings for an examination.

    Query Parameters:
        format (str): Response format - 'optimized' or 'multilingual' (default: 'optimized')
    """
    exam = get_object_or_404(Examination, id=examination_id)
    findings = exam.get_available_findings()

    response_format = request.query_params.get('format', 'optimized')
    if response_format == 'multilingual':
        return Response([
            build_multilingual_response(f)
            for f in findings
        ])
    else:
        serializer = FindingSerializer(findings, many=True)
        return Response(serializer.data)
    

def get_findings_for_patient_examination(request, patient_examination_id):
    """
    Retrieve findings for a specific PatientExamination.
    """
    # Assuming PatientExamination model has a related_name 'patient_findings' for its findings
    patient_examination = get_object_or_404(PatientExamination, id=patient_examination_id)
    findings = patient_examination.patient_findings.all()
    serializer = FindingSerializer(findings, many=True)
    return Response(serializer.data)

