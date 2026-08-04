from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.medical.examination.examination import Examination
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.serializers.patient.patient_dropdown import PatientDropdownSerializer
from endoreg_db.serializers.patient_examination import (
    PatientExaminationDraftResponseSerializer,
    PatientExaminationDraftSerializer,
    PatientExaminationSerializer,
)
from endoreg_db.serializers.examination import ExaminationDropdownSerializer


class PatientExaminationViewSet(viewsets.ModelViewSet):
    """
    ViewSet für PatientExamination mit vollständiger CRUD-Unterstützung
    """

    queryset = PatientExamination.objects.all().select_related("patient", "examination")
    serializer_class = PatientExaminationSerializer

    def get_queryset(self):
        """Optimierte Abfrage mit besserer Performance"""
        return (
            PatientExamination.objects.select_related("patient", "examination")
            .prefetch_related("patient_findings", "indications")
            .order_by("-date_start", "-id")
        )

    def get_patient_examination_ids(self):
        """Hilfsmethode zum Abrufen mehrerer PatientExamination IDs"""
        return PatientExamination.objects.filter(all=True).values_list("id", flat=True)

    def get_patient_examination_by_id(self, pk):
        """Hilfsmethode zum Abrufen einer PatientExamination nach ID"""
        if not PatientExamination.objects.filter(pk=pk).exists():
            return None
        else:
            return PatientExamination.objects.select_related(
                "patient", "examination"
            ).get(pk=pk)

    @action(detail=False, methods=["get"])
    def patients_dropdown(self, request):
        """
        Endpoint für Patient-Dropdown-Daten
        GET /api/patient-examinations/patients_dropdown/
        """
        patients = Patient.objects.all().order_by("first_name", "last_name")
        serializer = PatientDropdownSerializer(patients, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=["get"])
    def examinations_dropdown(self, request):
        """
        Endpoint für Examination-Dropdown-Daten
        GET /api/patient-examinations/examinations_dropdown/
        """
        examinations = Examination.objects.all().order_by("name")
        serializer = ExaminationDropdownSerializer(examinations, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=["get"])
    def recent(self, request):
        """
        Endpoint für die letzten PatientExaminations
        GET /api/patient-examinations/recent/
        """
        limit = int(request.query_params.get("limit", 10))
        recent_examinations = self.get_queryset()[:limit]
        serializer = self.get_serializer(recent_examinations, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["get"])
    def details(self, request, pk=None):
        """
        Detaillierte Informationen über eine PatientExamination
        GET /api/patient-examinations/{id}/details/
        """
        examination = self.get_object()
        data = {
            "examination": PatientExaminationSerializer(examination).data,
            "findings": examination.get_findings().count(),
            "indications": examination.get_indications().count(),
            "patient_age_at_examination": examination.get_patient_age_at_examination()
            if examination.date_start
            else None,
        }
        return Response(data)

    @action(detail=True, methods=["get", "put"])
    def draft(self, request, pk=None):
        """
        Draft endpoint for transient report editor state.

        GET /api/patient-examinations/{id}/draft/
        PUT /api/patient-examinations/{id}/draft/
        """
        examination = self.get_object()

        if request.method == "GET":
            serializer = PatientExaminationDraftResponseSerializer(
                {
                    "patient_examination_id": examination.id,
                    "draft": examination.report_draft or {},
                    "updated_at": examination.draft_updated_at,
                }
            )
            return Response(serializer.data)

        serializer = PatientExaminationDraftSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        examination.report_draft = serializer.validated_data
        examination.draft_updated_at = timezone.now()
        examination.save(update_fields=["report_draft", "draft_updated_at"])

        response_serializer = PatientExaminationDraftResponseSerializer(
            {
                "patient_examination_id": examination.id,
                "draft": examination.report_draft,
                "updated_at": examination.draft_updated_at,
            }
        )
        return Response(response_serializer.data, status=status.HTTP_200_OK)

    def create(self, request, *args, **kwargs):
        """
        Überschreibt die create-Methode für bessere Fehlerbehandlung
        """
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            try:
                self.perform_create(serializer)
                headers = self.get_success_headers(serializer.data)
                return Response(
                    serializer.data, status=status.HTTP_201_CREATED, headers=headers
                )
            except Exception as e:
                return Response(
                    {"error": f"Fehler beim Erstellen der Untersuchung: {str(e)}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def update(self, request, *args, **kwargs):
        """
        Überschreibt die update-Methode für bessere Fehlerbehandlung
        """
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)

        if serializer.is_valid():
            try:
                self.perform_update(serializer)
                return Response(serializer.data)
            except Exception as e:
                return Response(
                    {"error": f"Fehler beim Aktualisieren der Untersuchung: {str(e)}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def get_findings_for_examination(self, request, pk=None):
        """
        Endpoint to retrieve findings for a specific PatientExamination
        GET /api/patient-examinations/{pk}/findings/
        """
        examination = self.get_patient_examination_by_id(pk)
        if not examination:
            return Response(
                {"error": "PatientExamination nicht gefunden"},
                status=status.HTTP_404_NOT_FOUND,
            )

        findings = PatientExaminationSerializer.get_
        finding_data = [{"id": f.id, "name": str(f)} for f in findings]
        return Response(finding_data)
