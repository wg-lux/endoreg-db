from endoreg_db.models.other.gender import Gender
from rest_framework.viewsets import ReadOnlyModelViewSet
from rest_framework.permissions import IsAuthenticatedOrReadOnly
from ...serializers.administration import GenderSerializer


class GenderViewSet(ReadOnlyModelViewSet[Gender]):  # pyright: ignore[reportInvalidTypeArguments]
    """
    API endpoint for Gender options.
    Provides read-only access to gender choices for patient forms.
    """

    queryset = Gender.objects.all()
    serializer_class = GenderSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]
