from endoreg_db.models import Center
from endoreg_db.serializers.administration import CenterSerializer


from rest_framework import viewsets


class CenterViewSet(viewsets.ReadOnlyModelViewSet):
    """API endpoint für Center-Optionen (nur lesend)"""
    queryset = Center.objects.all()
    serializer_class = CenterSerializer
    #permission_classes = [IsAuthenticatedOrReadOnly]