
from rest_framework.viewsets import ReadOnlyModelViewSet
from rest_framework.permissions import IsAuthenticatedOrReadOnly
from endoreg_db.models import (
    Label,
)
from endoreg_db.serializers.label import LabelSerializer



class LabelViewSet(ReadOnlyModelViewSet):
    queryset = Label.objects.all()
    serializer_class = LabelSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

