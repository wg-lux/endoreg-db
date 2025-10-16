from endoreg_db.models import SensitiveMeta, VideoFile
from endoreg_db.serializers import SensitiveMetaUpdateSerializer
from endoreg_db.serializers.video.video_file_detail import VideoDetailSerializer
from endoreg_db.utils.permissions import EnvironmentAwarePermission
from endoreg_db.views.video.segmentation import _stream_video_file


from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView


import os

import logging
logger = logging.getLogger(__name__)


class VideoMediaView(APIView):
    """
    One endpoint that does
      GET /api/media/videos/          →   next video meta
      GET /api/media/videos/?last_id=7
      GET /api/media/videos/42/       →   meta for id 42
      GET /api/media/videos/42/  (Accept≠JSON)  →  byte‐range stream
      PATCH /api/media/videos/42/     →   update sensitive meta and handle raw file deletion
    """
    permission_classes = [EnvironmentAwarePermission]

    # ---------- GET ----------
    def get(self, request, pk=None):
        # Prüfe explizit auf Streaming-Anfrage via Query-Parameter
        wants_stream = request.query_params.get("stream") is not None or request.query_params.get("type") is not None
        
        if pk and wants_stream:                                    # STREAM
            vf = get_object_or_404(VideoFile, pk=pk)
            file_type = (request.query_params.get("type") or "auto").lower()
            
                
            return _stream_video_file(
                vf,
                os.getenv("FRONTEND_ORIGIN", "*"),
                file_type
            )

        # META (list or single) - nur wenn kein Streaming gewünscht
        if pk:                                                     # detail JSON
            vf = get_object_or_404(VideoFile, pk=pk)
        else:
            last_id = request.query_params.get("last_id")
            if last_id is not None:
                try:
                    last_id = int(last_id)
                except (ValueError, TypeError):
                    return Response(
                        {"error": "Invalid last_id parameter"},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            vf = VideoFile.objects.next_after(last_id)
            if not vf:
                return Response({"error": "No more videos"}, status=404)

        ser = VideoDetailSerializer(vf, context={"request": request})
        return Response(ser.data, status=status.HTTP_200_OK)

    # ---------- PATCH ----------
    @transaction.atomic
    def patch(self, request, pk=None):
        sm_id = request.data.get("sensitive_meta_id")
        if not sm_id:
            return Response(
                {"error": "sensitive_meta_id is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        try:
            sm_id = int(sm_id)
        except (ValueError, TypeError):
            return Response(
                {"error": "Invalid sensitive_meta_id"},
                status=status.HTTP_400_BAD_REQUEST
            )

        sm = get_object_or_404(SensitiveMeta, pk=sm_id)

        # Check if this is a validation acceptance (is_verified being set to True)
        is_accepting_validation = request.data.get("is_verified", False)
        delete_raw_files = request.data.get("delete_raw_files", False)

        # If user is accepting validation, automatically set delete_raw_files to True
        if is_accepting_validation:
            delete_raw_files = True
            logger.info(f"Validation accepted for SensitiveMeta {sm_id}, marking raw files for deletion")

        ser = SensitiveMetaUpdateSerializer(sm, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        updated_sm = ser.save()

        # Handle raw file deletion if requested or if validation was accepted
        if delete_raw_files and updated_sm.is_verified:
            try:
                # Find associated video file
                video_file = VideoFile.objects.filter(sensitive_meta=updated_sm).first()
                if video_file:
                    self._schedule_raw_file_deletion(video_file)
                    logger.info(f"Scheduled raw file deletion for video {video_file.uuid}")
                else:
                    logger.warning(f"No video file found for SensitiveMeta {sm_id}")
            except Exception as e:
                logger.error(f"Error scheduling raw file deletion for SensitiveMeta {sm_id}: {e}")
                # Don't fail the entire request if deletion scheduling fails

        return Response(ser.data, status=status.HTTP_200_OK)

    def _schedule_raw_file_deletion(self, video_file):
        """
        Schedule deletion of raw video file after validation acceptance.
        Uses the existing cleanup mechanism from the anonymization pipeline.
        """
        try:
            # Import here to avoid circular imports
            from django.db import transaction

            def cleanup_raw_files():
                """Cleanup function to be called after transaction commit"""
                try:
                    if hasattr(video_file, 'raw_video_file') and video_file.raw_video_file:
                        raw_file = video_file.raw_video_file
                        if raw_file.file and os.path.exists(raw_file.file.path):
                            logger.info(f"Deleting raw video file: {raw_file.file.path}")
                            os.remove(raw_file.file.path)
                            raw_file.file = None
                            raw_file.save()
                            logger.info(f"Successfully deleted raw video file for video {video_file.uuid}")
                        else:
                            logger.info(f"Raw video file already deleted or not found for video {video_file.uuid}")
                    else:
                        logger.info(f"No raw video file found for video {video_file.uuid}")

                    # Also delete any associated raw frames if they exist
                    if hasattr(video_file, 'raw_frames_dir'):
                        frames_dir = getattr(video_file, 'raw_frames_dir', None)
                        if frames_dir and os.path.exists(frames_dir):
                            import shutil
                            logger.info(f"Deleting raw frames directory: {frames_dir}")
                            shutil.rmtree(frames_dir)
                            logger.info(f"Successfully deleted raw frames for video {video_file.uuid}")

                except Exception as e:
                    logger.error(f"Error during raw file cleanup for video {video_file.uuid}: {e}")

            # Schedule cleanup after transaction commits
            transaction.on_commit(cleanup_raw_files)

        except Exception as e:
            logger.error(f"Error scheduling raw file deletion for video {video_file.uuid}: {e}")
            raise