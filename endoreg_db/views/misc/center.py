from __future__ import annotations

from endoreg_db.models.administration.center.center import Center
from endoreg_db.serializers.administration import CenterSerializer

from endoreg_db.utils.permissions import DEBUG_PERMISSIONS

from rest_framework import viewsets


class CenterViewSet(viewsets.ReadOnlyModelViewSet):
    """API endpoint für Center-Optionen (nur lesend)"""

    queryset = Center.objects.all()
    serializer_class = CenterSerializer
    permission_classes = DEBUG_PERMISSIONS
