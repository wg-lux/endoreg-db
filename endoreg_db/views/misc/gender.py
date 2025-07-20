from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticatedOrReadOnly
from ...models import Gender
from ...serializers.administration import GenderSerializer


class GenderViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint for Gender options.
    Provides read-only access to gender choices for patient forms.
    """
    queryset = Gender.objects.all()
    serializer_class = GenderSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]