"""Authenticated mask catalogue API; processing uses the existing correction API."""

from collections.abc import Sequence

from lx_dtypes.models.contracts.video_masks import VideoMaskProfile
from pydantic import ValidationError
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.helpers.typing import CompositePermissionClass
from endoreg_db.openapi import OpenApiAPIView
from endoreg_db.services.video_files.video_masks import (
    create_video_mask,
    list_video_masks,
)


class VideoMaskCoordinatesSerializer(serializers.Serializer[dict[str, object]]):
    x = serializers.IntegerField(min_value=0)
    y = serializers.IntegerField(min_value=0)
    width = serializers.IntegerField(min_value=1)
    height = serializers.IntegerField(min_value=1)
    image_width = serializers.IntegerField(min_value=1)
    image_height = serializers.IntegerField(min_value=1)


class VideoMaskProfileSerializer(serializers.Serializer[dict[str, object]]):
    name = serializers.RegexField(r"^[a-z0-9][a-z0-9_-]{0,79}$")
    # DRF consumes this declaration into fields; it is not Serializer.label.
    label = serializers.CharField(max_length=160)  # pyright: ignore[reportAssignmentType]
    device_name = serializers.CharField(max_length=160)
    mask = VideoMaskCoordinatesSerializer()


class VideoMaskCatalogueSerializer(serializers.Serializer[dict[str, object]]):
    masks = VideoMaskProfileSerializer(many=True)


class VideoMasksView(OpenApiAPIView):
    """List or create immutable device mask profiles."""

    permission_classes: Sequence[CompositePermissionClass] = (
        IsAuthenticated,
        PolicyPermission,
    )

    def get_serializer(
        self, *args: object, **kwargs: object
    ) -> serializers.Serializer[dict[str, object]]:
        # Runtime validation remains owned by the shared Pydantic contract.
        if self.request.method == "GET":
            return VideoMaskCatalogueSerializer()
        return VideoMaskProfileSerializer()

    def get(self, request: Request) -> Response:
        return Response(
            {"masks": [mask.model_dump(mode="json") for mask in list_video_masks()]}
        )

    def post(self, request: Request) -> Response:
        try:
            profile = VideoMaskProfile.model_validate(request.data)
        except ValidationError:
            return Response({"error": "Invalid mask name or coordinates"}, status=400)
        try:
            created = create_video_mask(profile)
        except FileExistsError:
            return Response(
                {"error": "A mask with this name already exists"}, status=409
            )
        return Response(created.model_dump(mode="json"), status=201)
