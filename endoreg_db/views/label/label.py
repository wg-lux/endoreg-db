
from rest_framework.viewsets import ReadOnlyModelViewSet
from rest_framework.permissions import IsAuthenticatedOrReadOnly
from rest_framework.decorators import action
from rest_framework.response import Response
from endoreg_db.models import (
    Label,
)
from ...serializers.finding_classification import (
    # LabelSerializer, #TODO
    FindingClassificationChoiceSerializer,
)


class LabelViewSet(ReadOnlyModelViewSet):
    queryset = Label.objects.all()
    # serializer_class = LabelSerializer # TODO
    permission_classes = [IsAuthenticatedOrReadOnly]

