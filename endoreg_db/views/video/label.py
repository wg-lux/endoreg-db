# endoreg_db/views/media/label_media.py
import logging
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework import status

from endoreg_db.models import Label
from endoreg_db.serializers.label.label import LabelSerializer
from endoreg_db.utils.permissions import EnvironmentAwarePermission
# from rest_framework.permissions import IsAuthenticated
# from endoreg_db.authz.permissions import PolicyPermission

logger = logging.getLogger(__name__)


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission])
# or: @permission_classes([IsAuthenticated, PolicyPermission])
def label_list(request):
    """
    List all annotation labels used for video segments.

    GET /api/media/labels/
    Response:
    [
      { "id": 1, "name": "polyp" },
      ...
    ]
    """
    try:
        labels = Label.objects.all().order_by("name")
        serializer = LabelSerializer(labels, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
    except Exception as e:
        logger.error(f"Error fetching labels: {e}")
        return Response(
            {"error": "Failed to fetch labels"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
