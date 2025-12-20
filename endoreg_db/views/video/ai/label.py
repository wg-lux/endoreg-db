# endoreg_db/views/media/label_media.py
import logging
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework import status

from endoreg_db.models import Label
from endoreg_db.serializers.label_video_segment.label import LabelSerializer
from endoreg_db.utils.permissions import EnvironmentAwarePermission
# from rest_framework.permissions import IsAuthenticated
# from endoreg_db.authz.permissions import PolicyPermission

logger = logging.getLogger(__name__)


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission])
# or: @permission_classes([IsAuthenticated, PolicyPermission])
def label_list(request) -> Response:
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


@api_view(["POST"])
@permission_classes([EnvironmentAwarePermission])
def add_label(request) -> Response:
    try:
        name = request.data.get("name")
        if not name:
            return Response(
                {"error": "Field 'name' is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        label, created = Label.get_or_create_from_name(name)

        return Response(
            {
                "success": "label added to database"
                if created
                else "label already existed",
                "id": label.id,
                "name": label.name,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
    except Exception as e:
        logger.error(f"Error creating label: {e}")
        return Response(
            {"error": "Failed to create label"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["DELETE"])
def delete_label(request) -> Response:
    try:
        name = request.data.get("name")
        if not name:
            return Response(
                {"error": "Field 'name' is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        Label.delete(name)
        if isinstance(Label.get_or_create_from_name(name), Label):
            return Response(
                {"error": "Field not deleted"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        else:
            return Response(
                {"success": f"label {name} deleted"}, status=status.HTTP_200_OK
            )
    except Exception as e:
        logger.error(f"Error creating label: {e}")
        return Response(
            {"error": "Failed to create label"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["PATCH", "POST"])
@permission_classes([EnvironmentAwarePermission])
def update_label(request) -> Response:
    """
    Update/rename a label.

    Body:
    {
      "name_old": "polyp_old",
      "name": "polyp"
    }
    """
    name_old = request.data.get("name_old")
    new_name = request.data.get("name")

    if not name_old:
        return Response(
            {"error": "Field 'name_old' is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not new_name:
        return Response(
            {"error": "Field 'name' is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        try:
            label = Label.objects.get(name=name_old)
        except Label.DoesNotExist:
            return Response(
                {"error": f"Label '{name_old}' not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Optional: handle duplicate target names
        if Label.objects.filter(name=new_name).exclude(pk=label.pk).exists():
            return Response(
                {"error": f"Label '{new_name}' already exists"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        label.name = new_name
        label.save()

        return Response(
            {
                "success": f"Label '{name_old}' renamed to '{new_name}'",
                "id": label.id,
                "name": label.name,
            },
            status=status.HTTP_200_OK,
        )
    except Exception as e:
        logger.error(f"Error updating label '{name_old}' → '{new_name}': {e}")
        return Response(
            {"error": "Failed to update label"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
