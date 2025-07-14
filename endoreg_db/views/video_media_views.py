# views/media_video.py   (move out of "...raw_video_meta_validation_views.py")
from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
import os

from ..models import VideoFile, SensitiveMeta
from ..serializers.video_serializer import VideoDetailSer, SensitiveMetaUpdateSer
from .video_segmentation_views import _stream_video_file
from ..utils.permissions import EnvironmentAwarePermission


class VideoMediaView(APIView):
    """
    One endpoint that does
      GET /api/media/videos/          →   next video meta
      GET /api/media/videos/?last_id=7
      GET /api/media/videos/42/       →   meta for id 42
      GET /api/media/videos/42/  (Accept≠JSON)  →  byte‐range stream
      PATCH /api/media/videos/42/     →   update sensitive meta
    """
    permission_classes = [EnvironmentAwarePermission]

    # ---------- GET ----------
    def get(self, request, pk=None):
        wants_json = request.accepted_media_type == "application/json"

        if pk and not wants_json:                                  # STREAM
            vf = get_object_or_404(VideoFile, pk=pk)
            return _stream_video_file(
                vf,
                os.getenv("FRONTEND_ORIGIN", "*")
            )

        # META (list or single)
        if pk:                                                     # detail JSON
            vf = get_object_or_404(VideoFile, pk=pk)
        else:                                                      # next / list
            last_id = request.query_params.get("last_id")
            vf = VideoFile.objects.next_after(last_id)
            if not vf:
                return Response({"error": "No more videos"}, status=404)

        ser = VideoDetailSer(vf, context={"request": request})
        return Response(ser.data, status=status.HTTP_200_OK)

    # ---------- PATCH ----------
    def patch(self, request, pk=None):
        sm_id = request.data.get("sensitive_meta_id")
        sm = get_object_or_404(SensitiveMeta, pk=sm_id)
        ser = SensitiveMetaUpdateSer(sm, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data, status=status.HTTP_200_OK)
