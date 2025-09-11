from endoreg_db.models import PatientFinding
from endoreg_db.serializers import PatientFindingSerializer
from rest_framework.response import Response

from rest_framework.viewsets import ModelViewSet


class PatientFindingViewSet(ModelViewSet):
    queryset = PatientFinding.objects.all()
    serializer_class = PatientFindingSerializer
    
    def get_patient_findings(self, request, pk=None):
        """
        Endpoint to retrieve findings for a specific PatientExamination
        GET /api/patient_examinations/{pk}/findings/
        """
        findings = PatientFinding.objects.filter(patient_examination_id=pk)
        serializer = self.get_serializer(findings, many=True)
        return Response(serializer.data)