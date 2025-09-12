from endoreg_db.models import PatientFinding
from endoreg_db.serializers import PatientFindingSerializer
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet
from rest_framework.decorators import action
from django_filters.rest_framework import DjangoFilterBackend


class PatientFindingViewSet(ModelViewSet):
    queryset = PatientFinding.objects.all()
    serializer_class = PatientFindingSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['patient_examination']
    
    @action(detail=False, methods=['get'], url_path='by-examination/(?P<patient_examination_id>[^/.]+)')
    def get_patient_findings_by_examination(self, request, patient_examination_id=None):
        """Explicitly get patient findings by patient examination id_

        Args:
            request (_type_): _description_
            patient_examination_id (_type_, optional): _description_. Defaults to None.

        Returns:
            _type_: _description_
        """
        patient_finding_objs = PatientFinding.objects.filter(patient_examination=patient_examination_id)
        serializer = self.get_serializer(patient_finding_objs, many=True)
        return Response(serializer.data)
    

    @action(detail=False, methods=['get'], url_path='by-id/(?P<patient_finding_id>[^/.]+)')
    def get_patient_finding_by_id(self, request, patient_finding_id=None):
        """_summary_

        Args:
            request (_type_): _description_
            patient_finding_id (_type_, optional): _description_. Defaults to None.

        Returns:
            _type_: _description_
        """
        patient_finding_obj = PatientFinding.objects.filter(id=patient_finding_id)
        serializer = self.get_serializer(patient_finding_obj, many=True)
        return Response(serializer.data)
    
    def add_patient_finding(self, request):
        """
        Endpoint to add a new finding to a PatientExamination
        POST /api/patient_findings/add/
        """
        
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=201)
    
    def get_all_patient_findings(self, request):
        """
        Endpoint to retrieve all PatientFindings
        GET /api/patient_findings/
        """
        findings = PatientFinding.objects.all()
        serializer = self.get_serializer(findings, many=True)
        return Response(serializer.data)