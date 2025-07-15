# views/media_video.py   (move out of "...raw_video_meta_validation_views.py")
from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
import os
import json
from celery import current_app
from django.conf import settings

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
        ser = SensitiveMetaUpdateSer(sm, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data, status=status.HTTP_200_OK)


class VideoCorrectionView(APIView):
    """
    GET /api/video-correction/{id}/ - Get video details for correction
    """
    permission_classes = [EnvironmentAwarePermission]
    
    def get(self, request, pk):
        video = get_object_or_404(VideoFile, pk=pk)
        ser = VideoDetailSer(video, context={"request": request})
        return Response(ser.data, status=status.HTTP_200_OK)


class VideoMetadataView(APIView):
    """
    GET /api/video-metadata/{id}/ - Get video metadata including sensitivity analysis
    """
    permission_classes = [EnvironmentAwarePermission]
    
    def get(self, request, pk):
        video = get_object_or_404(VideoFile, pk=pk)
        
        # Get or calculate metadata
        metadata = {
            'sensitiveFrameCount': getattr(video, 'sensitive_frame_count', None),
            'totalFrames': getattr(video, 'total_frames', None),
            'sensitiveRatio': getattr(video, 'sensitive_ratio', None),
            'duration': getattr(video, 'duration', None),
            'resolution': getattr(video, 'resolution', None),
        }
        
        return Response(metadata, status=status.HTTP_200_OK)


class VideoProcessingHistoryView(APIView):
    """
    GET /api/video-processing-history/{id}/ - Get processing history for a video
    """
    permission_classes = [EnvironmentAwarePermission]
    
    def get(self, request, pk):
        video = get_object_or_404(VideoFile, pk=pk)
        
        # For now, return empty history - can be extended with actual history tracking
        history = []
        
        return Response(history, status=status.HTTP_200_OK)


class VideoAnalyzeView(APIView):
    """
    POST /api/video-analyze/{id}/ - Analyze video for sensitive content
    """
    permission_classes = [EnvironmentAwarePermission]
    
    def post(self, request, pk):
        video = get_object_or_404(VideoFile, pk=pk)
        use_minicpm = request.data.get('use_minicpm', True)
        detailed_analysis = request.data.get('detailed_analysis', True)
        
        try:
            # Import FrameCleaner here to avoid circular imports
            from lx_anonymizer.frame_cleaner import FrameCleaner
            
            cleaner = FrameCleaner(
                video_path=video.file.path,
                use_minicpm=use_minicpm
            )
            
            # Perform analysis
            analysis_result = cleaner.analyze_video_sensitivity()
            
            # Update video metadata
            if hasattr(video, 'sensitive_frame_count'):
                video.sensitive_frame_count = analysis_result.get('sensitive_frames', 0)
            if hasattr(video, 'total_frames'):
                video.total_frames = analysis_result.get('total_frames', 0)
            if hasattr(video, 'sensitive_ratio'):
                video.sensitive_ratio = analysis_result.get('sensitivity_ratio', 0.0)
            
            try:
                video.save()
            except Exception as e:
                print(f"Warning: Could not save video metadata: {e}")
            
            return Response(analysis_result, status=status.HTTP_200_OK)
            
        except Exception as e:
            return Response(
                {"error": f"Analysis failed: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class VideoApplyMaskView(APIView):
    """
    POST /api/video-apply-mask/{id}/ - Apply mask to video using streaming processing
    """
    permission_classes = [EnvironmentAwarePermission]
    
    def post(self, request, pk):
        video = get_object_or_404(VideoFile, pk=pk)
        
        mask_type = request.data.get('mask_type', 'device_default')
        device_name = request.data.get('device_name', 'olympus_cv_1500')
        use_streaming = request.data.get('use_streaming', True)
        custom_mask = request.data.get('custom_mask')
        
        try:
            # Start async task for video masking
            from endoreg_db.tasks.video_processing_tasks import apply_video_mask_task
            
            task_data = {
                'video_id': pk,
                'mask_type': mask_type,
                'device_name': device_name,
                'use_streaming': use_streaming,
                'custom_mask': custom_mask
            }
            
            task = apply_video_mask_task.delay(**task_data)
            
            return Response(
                {"task_id": task.id, "status": "started"},
                status=status.HTTP_202_ACCEPTED
            )
            
        except Exception as e:
            return Response(
                {"error": f"Failed to start masking task: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class VideoRemoveFramesView(APIView):
    """
    POST /api/video-remove-frames/{id}/ - Remove frames from video using streaming processing
    """
    permission_classes = [EnvironmentAwarePermission]
    
    def post(self, request, pk):
        video = get_object_or_404(VideoFile, pk=pk)
        
        selection_method = request.data.get('selection_method', 'automatic')
        detection_engine = request.data.get('detection_engine', 'minicpm')
        use_streaming = request.data.get('use_streaming', True)
        manual_frames = request.data.get('manual_frames')
        
        try:
            # Start async task for frame removal
            from endoreg_db.tasks.video_processing_tasks import remove_video_frames_task
            
            task_data = {
                'video_id': pk,
                'selection_method': selection_method,
                'detection_engine': detection_engine,
                'use_streaming': use_streaming,
                'manual_frames': manual_frames
            }
            
            task = remove_video_frames_task.delay(**task_data)
            
            return Response(
                {"task_id": task.id, "status": "started"},
                status=status.HTTP_202_ACCEPTED
            )
            
        except Exception as e:
            return Response(
                {"error": f"Failed to start frame removal task: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class VideoReprocessView(APIView):
    """
    POST /api/video-reprocess/{id}/ - Reprocess video with updated settings
    """
    permission_classes = [EnvironmentAwarePermission]
    
    def post(self, request, pk):
        video = get_object_or_404(VideoFile, pk=pk)
        
        try:
            # Reset video processing status
            if hasattr(video, 'processing_status'):
                video.processing_status = 'not_started'
                video.save()
            
            # Start reprocessing task
            from endoreg_db.tasks.video_processing_tasks import reprocess_video_task
            
            task = reprocess_video_task.delay(video_id=pk)
            
            return Response(
                {"task_id": task.id, "status": "reprocessing_started"},
                status=status.HTTP_202_ACCEPTED
            )
            
        except Exception as e:
            return Response(
                {"error": f"Failed to start reprocessing: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class TaskStatusView(APIView):
    """
    GET /api/task-status/{task_id}/ - Get status of async task
    """
    permission_classes = [EnvironmentAwarePermission]
    
    def get(self, request, task_id):
        try:
            task_result = current_app.AsyncResult(task_id)
            
            response_data = {
                'task_id': task_id,
                'status': task_result.status,
                'progress': 0,
                'message': 'Task pending...'
            }
            
            if task_result.status == 'PENDING':
                response_data['message'] = 'Task is waiting to be processed'
            elif task_result.status == 'PROGRESS':
                response_data.update(task_result.result or {})
            elif task_result.status == 'SUCCESS':
                response_data.update({
                    'progress': 100,
                    'message': 'Task completed successfully',
                    'result': task_result.result
                })
            elif task_result.status == 'FAILURE':
                response_data.update({
                    'message': str(task_result.result),
                    'error': True
                })
            
            return Response(response_data, status=status.HTTP_200_OK)
            
        except Exception as e:
            return Response(
                {"error": f"Failed to get task status: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class VideoDownloadProcessedView(APIView):
    """
    GET /api/video-download-processed/{id}/ - Download processed video result
    """
    permission_classes = [EnvironmentAwarePermission]
    
    def get(self, request, pk):
        video = get_object_or_404(VideoFile, pk=pk)
        output_path = request.query_params.get('path')
        
        if not output_path or not os.path.exists(output_path):
            return Response(
                {"error": "Processed file not found"},
                status=status.HTTP_404_NOT_FOUND
            )
        
        try:
            from django.http import FileResponse
            response = FileResponse(
                open(output_path, 'rb'),
                content_type='video/mp4'
            )
            response['Content-Disposition'] = f'attachment; filename="{os.path.basename(output_path)}"'
            return response
            
        except Exception as e:
            return Response(
                {"error": f"Failed to serve file: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
