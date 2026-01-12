from endoreg_db.models import VideoFile
from endoreg_db.utils.permissions import EnvironmentAwarePermission
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

class VideoMetadataStatsView(APIView):
    """
    GET media/videos/{pk}/metadata/ - Get comprehensive video metadata.
    
    Merges logic from:
    1. VideoFile model (duration, fps, resolution)
    2. VideoState model (status, anonymization flags)
    3. Related models (Center, Processor, SensitiveMeta)
    """

    permission_classes = [EnvironmentAwarePermission]

    def get(self, request, pk):
        # Prefetch related fields to avoid N+1 queries since we access them all
        video = get_object_or_404(
            VideoFile.objects.select_related(
                'state', 
                'center', 
                'processor', 
                'video_meta', 
                'sensitive_meta'
            ), 
            pk=pk
        )

        # --- 1. Basic Specs (from VideoFile) ---
        # Use model fields, defaulting if None
        duration = video.duration if video.duration is not None else 0
        fps = video.fps if video.fps is not None else 50 
        
        resolution = "BLANK"
        if video.width and video.height:
            resolution = f"{video.width}x{video.height}"

        # --- 2. Status & State (from VideoState) ---
        status_val = "BLANK"
        is_anonymized = False
        
        if hasattr(video, 'state') and video.state:
            # anonymization_status returns an Enum, we need the value (string)
            raw_status = video.state.anonymization_status
            status_val = getattr(raw_status, 'value', str(raw_status))
            
            # Check logical anonymization state
            is_anonymized = (
                status_val == 'done_processing_anonymization' or 
                video.state.anonymized
            )

        # --- 3. Relations (Center / Processor) ---
        center_name = "Unbekannt"
        if video.center:
            center_name = getattr(video.center, 'name', str(video.center))

        processor_name = "Unbekannt"
        if video.processor:
            processor_name = getattr(video.processor, 'name', str(video.processor))

        # --- 4. Deep Inference (SensitiveMeta / VideoMeta) ---
        sensitive_count = None
        total_frames = video.frame_count # Direct from VideoFile model
        sensitive_ratio = None
        outside_frame_count = 0
        
        outside_segments = video.get_outside_segments(only_validated=False)
        count = outside_segments.count()

        print(f"Number of outside segments: {count}")
        # Try to get ROI data (from video_meta relation)
        has_roi = False
        if hasattr(video, 'video_meta') and video.video_meta:
            has_roi = getattr(video.video_meta, 'has_roi', False)

        # --- 5. Construct Response ---
        metadata = {
            # -- Frontend VideoMeta Requirements --
            "id": video.pk,
            "original_file_name": video.original_file_name or f"Video {pk}",
            "status": str(status_val),
            "assignedUser": "BLANK",
            "anonymized": is_anonymized,
            "duration": duration,
            "fps": fps,
            "hasROI": has_roi,
            "outsideFrameCount": outside_frame_count,
            "centerName": center_name,
            "processorName": processor_name,
            "sensitiveFrameCount": sensitive_count,
            "totalFrames": total_frames,
            "sensitiveRatio": sensitive_ratio,
            "resolution": resolution,
        }

        return Response(metadata, status=status.HTTP_200_OK)