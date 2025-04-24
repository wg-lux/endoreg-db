from rest_framework import viewsets
from rest_framework.views import APIView
from rest_framework.response import Response
from endoreg_db.models import (
    Examination,
    FindingMorphologyClassificationChoice as MorphologyClassificationChoice,
    FindingLocationClassificationChoice as LocationClassificationChoice,
    FindingIntervention as Intervention,
    Instrument
)
from endoreg_db.serializers.examination import ExaminationSerializer

class ExaminationViewSet(viewsets.ModelViewSet):
    queryset = Examination.objects.all()
    serializer_class = ExaminationSerializer
    permission_classes = []  # Anpassung nach Bedarf

# Sub‑Category Endpunkte
class MorphologyChoiceListView(APIView):
    def get(self, request, exam_id):
        choices = MorphologyClassificationChoice.objects.filter(examination_id=exam_id)
        # Minimal: direkter JSON Output
        data = [{"id": c.id, "name": c.name, "classificationId": c.classificationId} for c in choices]
        return Response(data)

class LocationChoiceListView(APIView):
    def get(self, request, exam_id):
        choices = LocationClassificationChoice.objects.filter(examination_id=exam_id)
        data = [{"id": c.id, "name": c.name, "classificationId": c.classificationId} for c in choices]
        return Response(data)

class InterventionListView(APIView):
    def get(self, request, exam_id):
        interventions = Intervention.objects.filter(examination_id=exam_id)
        data = [{"id": i.id, "name": i.name} for i in interventions]
        return Response(data)

class InstrumentListView(APIView):
    def get(self, request, exam_id):
        instruments = Instrument.objects.filter(examination_id=exam_id)
        data = [{"id": i.id, "name": i.name} for i in instruments]
        return Response(data)
