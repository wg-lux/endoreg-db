from endoreg_db.models import PatientFinding
from endoreg_db.serializers import PatientFindingSerializer


from rest_framework.viewsets import ModelViewSet


class PatientFindingViewSet(ModelViewSet):
    queryset = PatientFinding.objects.all()
    serializer_class = PatientFindingSerializer