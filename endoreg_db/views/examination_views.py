from rest_framework import viewsets
from endoreg_db.models import Examination
from endoreg_db.serializers.examination import ExaminationSerializer

class ExaminationViewSet(viewsets.ModelViewSet):
   
   
    queryset = Examination.objects.all()
    serializer_class = ExaminationSerializer