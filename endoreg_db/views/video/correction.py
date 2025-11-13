"""
Video Correction API Views

Provides endpoints for video analysis, masking, frame removal, and processing history.
Created as part of Phase 1.1: Video Correction API Endpoints.

Available Functions from lx_anonymizer (already implemented):
- FrameCleaner.analyze_video_sensitivity() - Frame analysis
- FrameCleaner.clean_video() - Complete anonymization
- FrameCleaner._mask_video() - FFmpeg masking
- FrameCleaner.remove_frames_from_video() - Frame removal
- VideoImportService._get_processor_roi_info() - ROI extraction
"""
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.shortcuts import get_object_or_404
from django.conf import settings
from pathlib import Path
import json
import logging

from endoreg_db.models import VideoFile, VideoMetadata, VideoProcessingHistory, LabelVideoSegment
from endoreg_db.serializers import VideoMetaSerializer, VideoProcessingHistorySerializer
from lx_anonymizer import FrameCleaner

from endoreg_db.models import VideoFile
from endoreg_db.serializers.video.video_file_detail import VideoDetailSerializer
from endoreg_db.utils.permissions import EnvironmentAwarePermission

logger = logging.getLogger(__name__)

def update_processed_file(video, output_path: Path):
    from endoreg_db.utils import data_paths
    storage_root = Path(data_paths["storage"])
    try:
        rel_path = output_path.relative_to(storage_root)
    except ValueError:
        rel_path = output_path.relative_to(Path(settings.MEDIA_ROOT))
    video.processed_file.name = str(rel_path)
    video.save(update_fields=["processed_file"])


class VideoCorrectionView(APIView):
    """
    GET /api/video/media/video-correction/{id}/ - Get video details for correction
    """
    permission_classes = [EnvironmentAwarePermission]

    def get(self, request, pk):
        video = get_object_or_404(VideoFile, pk=pk)
        ser = VideoDetailSerializer(video, context={"request": request})
        return Response(ser.data, status=status.HTTP_200_OK)

def update_segments_after_frame_removal(video: VideoFile, removed_frames: list) -> dict:
    """
    Update LabelVideoSegment frame boundaries after frame removal.
    
    This function shifts segment start/end frames based on which frames were removed.
    Segments are deleted if all their frames are removed.
    
    Args:
        video: VideoFile instance whose segments should be updated
        removed_frames: List of frame numbers that were removed (sorted)
    
    Returns:
        dict: {
            'segments_updated': int,
            'segments_deleted': int,
            'segments_unchanged': int
        }
    
    Algorithm:
        For each segment:
        1. Count frames removed before segment → shift start_frame
        2. Count frames removed within segment → shift end_frame
        3. Delete segment if start_frame >= end_frame (all frames removed)
    
    Example:
        Original segment: frames 100-200
        Removed frames: [50, 75, 120, 150, 180]
        
        Frames before segment (< 100): 2 frames (50, 75)
        Frames within segment (100-200): 3 frames (120, 150, 180)
        
        New segment: frames (100-2) to (200-2-3) = 98-195
    """
    if not removed_frames:
        return {'segments_updated': 0, 'segments_deleted': 0, 'segments_unchanged': 0}
    
    removed_frames = sorted(set(removed_frames))  # Ensure sorted and unique
    segments = LabelVideoSegment.objects.filter(
        video_file=video
    ).order_by('start_frame_number')
    
    segments_updated = 0
    segments_deleted = 0
    segments_unchanged = 0
    
    for segment in segments:
        original_start = segment.start_frame_number
        original_end = segment.end_frame_number
        
        # Count frames removed before this segment
        frames_before = sum(1 for f in removed_frames if f < original_start)
        
        # Count frames removed within this segment
        frames_within = sum(1 for f in removed_frames if original_start <= f <= original_end)
        
        # Calculate new boundaries
        new_start = original_start - frames_before
        new_end = original_end - frames_before - frames_within
        
        # Delete segment if all frames were removed
        if new_start >= new_end:
            logger.info(
                f"Deleting segment {segment.id} (original: {original_start}-{original_end}) "
                f"- all {frames_within} frames removed"
            )
            segment.delete()
            segments_deleted += 1
        elif new_start != original_start or new_end != original_end:
            # Update segment boundaries
            logger.info(
                f"Updating segment {segment.id}: "
                f"{original_start}-{original_end} → {new_start}-{new_end} "
                f"(before: {frames_before}, within: {frames_within})"
            )
            segment.start_frame_number = new_start
            segment.end_frame_number = new_end
            segment.save(update_fields=["start_frame_number", "end_frame_number"])
            segments_updated += 1
        else:
            # No change needed
            segments_unchanged += 1
    
    logger.info(
        f"Segment update complete for video {video.id}: "
        f"{segments_updated} updated, {segments_deleted} deleted, {segments_unchanged} unchanged"
    )
    
    return {
        'segments_updated': segments_updated,
        'segments_deleted': segments_deleted,
        'segments_unchanged': segments_unchanged
    }


class VideoMetadataView(APIView):
    """
    GET /api/media/videos/{pk}/metadata/
    
    Retrieve analysis results for a video.
    
    Returns:
        {
            "id": 1,
            "video": 123,
            "sensitive_frame_count": 42,
            "sensitive_ratio": 0.05,
            "sensitive_frame_ids": "[10, 15, 20, ...]",
            "sensitive_frame_ids_list": [10, 15, 20, ...],
            "sensitive_percentage": 5.0,
            "has_analysis": true,
            "analyzed_at": "2025-10-09T10:00:00Z"
        }
    """
    
    def get(self, request, pk):
        """Get video metadata by video ID."""
        video = get_object_or_404(VideoFile, pk=pk)
        
        # Get or create metadata record
        metadata, created = VideoMetadata.objects.get_or_create(video=video)
        
        serializer = VideoMetaSerializer(metadata, context={'request': request})
        return Response(serializer.data)


class VideoProcessingHistoryView(APIView):
    """
    GET /api/media/videos/{pk}/processing-history/
    
    Retrieve processing history for a video.
    
    Returns list of processing operations:
        [
            {
                "id": 1,
                "video": 123,
                "operation": "mask_overlay",
                "operation_display": "Mask Overlay",
                "status": "success",
                "status_display": "Success",
                "config": {"mask_type": "device", "device_name": "olympus"},
                "output_file": "/path/to/masked_video.mp4",
                "download_url": "/api/media/processed-videos/123/1/",
                "details": "",
                "task_id": "abc-123-def",
                "created_at": "2025-10-09T10:00:00Z",
                "completed_at": "2025-10-09T10:05:00Z",
                "duration": 300.5,
                "is_complete": true
            },
            ...
        ]
    """
    
    def get(self, request, pk):
        """Get processing history for a video."""
        video = get_object_or_404(VideoFile, pk=pk)
        
        # Get all history records, newest first
        history = VideoProcessingHistory.objects.filter(
            video=video
        ).order_by('-created_at')
        
        serializer = VideoProcessingHistorySerializer(
            history, 
            many=True, 
            context={'request': request}
        )
        return Response(serializer.data)


class VideoApplyMaskView(APIView):
    """
    POST /api/media/videos/{pk}/apply-mask/
    
    Apply device mask or custom ROI mask to video.
    
    Request body:
        {
            "mask_type": "device",           // or "custom"
            "device_name": "olympus",        // required if mask_type=device
            "roi": {                         // required if mask_type=custom
                "x": 550,
                "y": 0,
                "width": 1350,
                "height": 1080
            },
            "processing_method": "streaming" // or "direct" (default: streaming)
        }
    
    Returns:
        {
            "task_id": "abc-123-def",        // Celery task ID (future)
            "output_file": "/path/to/masked_video.mp4",
            "message": "Masking complete",
            "processing_time": 125.5
        }
    
    Note: Currently synchronous. Will be converted to Celery task in Phase 1.2.
    """
    
    def post(self, request, pk):
        """Apply masking to video."""
        video = get_object_or_404(VideoFile, pk=pk)
        
        # Extract parameters
        mask_type = request.data.get('mask_type', 'device')
        device_name = request.data.get('device_name')
        roi = request.data.get('roi')
        processing_method = request.data.get('processing_method', 'streaming')
        
        # Validate required parameters
        if mask_type == 'device' and not device_name:
            return Response(
                {'error': 'device_name required for device mask'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if mask_type == 'custom' and not roi:
            return Response(
                {'error': 'roi required for custom mask'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Create processing history record
        history = VideoProcessingHistory.objects.create(
            video=video,
            operation=VideoProcessingHistory.OPERATION_MASKING,
            status=VideoProcessingHistory.STATUS_PENDING,
            config={
                'mask_type': mask_type,
                'device_name': device_name,
                'roi': roi,
                'processing_method': processing_method
            }
        )
        
        try:
            history.mark_running()
            
            # Initialize FrameCleaner
            frame_cleaner = FrameCleaner()
            
            # Get video paths
            video_path = Path(video.raw_file.path) if hasattr(video.raw_file, 'path') else Path(str(video.raw_file))
            output_path = Path(settings.MEDIA_ROOT) / 'anonym_videos' / f"{video.uuid}_masked.mp4"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Load or create mask config
            if mask_type == 'device':
                # Load device-specific mask from lx_anonymizer/masks/
                mask_config = frame_cleaner._load_mask(device_name)
            else:  # custom
                # Convert ROI to mask config
                mask_config = frame_cleaner._create_mask_config_from_roi(
                    endoscope_roi=roi,
                )
            
            # Apply mask (uses existing FrameCleaner._mask_video)
            import time
            start_time = time.time()
            
            success = frame_cleaner._mask_video(
                input_video=video_path,
                mask_config=mask_config,
                output_video=output_path
            )
            
            processing_time = time.time() - start_time
            
            if success:
                # Update video record with anonymized file
                from django.core.files import File
                processed_file_path = output_path
                update_processed_file(video, processed_file_path)
                # Mark history as success
                history.mark_success(
                    output_file=str(output_path),
                    details=f"Masking completed in {processing_time:.1f}s"
                )
                
                logger.info(f"Video {pk} masked successfully: {output_path}")
                
                return Response({
                    'task_id': None,  # Will be Celery task ID in Phase 1.2
                    'output_file': str(output_path),
                    'message': 'Masking complete',
                    'processing_time': processing_time
                })
            else:
                raise Exception("Masking failed - check FFmpeg logs")
                
        except Exception as e:
            logger.error(f"Video masking failed for {pk}: {str(e)}", exc_info=True)
            
            history.mark_failure(str(e))
            
            return Response(
                {'error': f'Masking failed: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class VideoRemoveFramesView(APIView):
    """
    POST /api/media/videos/{pk}/remove-frames/
    
    Remove specified frames from video.
    
    Request body:
        {
            "frame_list": [10, 15, 20, 30],      // explicit frame numbers
            // OR
            "frame_ranges": "10-20,30,45-50",    // range string
            // OR
            "detection_method": "automatic",     // use analysis results
            
            "processing_method": "streaming"     // or "traditional"
        }
    
    Returns:
        {
            "task_id": "abc-123-def",
            "output_file": "/path/to/cleaned_video.mp4",
            "frames_removed": 42,
            "message": "Frame removal complete",
            "processing_time": 180.3
        }
    
    Note: Currently synchronous. Will be converted to Celery task in Phase 1.2.
    """
    
    def post(self, request, pk):
        """Remove frames from video."""
        video = get_object_or_404(VideoFile, pk=pk)
        
        # Extract parameters
        frame_list = request.data.get('frame_list')
        frame_ranges = request.data.get('frame_ranges')
        detection_method = request.data.get('detection_method')
        processing_method = request.data.get('processing_method', 'streaming')
        
        # Determine frames to remove
        frames_to_remove = []
        
        if frame_list:
            frames_to_remove = frame_list
        elif frame_ranges:
            frames_to_remove = self._parse_frame_ranges(frame_ranges)
        elif detection_method == 'automatic':
            # Use existing analysis results
            try:
                metadata = VideoMetadata.objects.get(video=video)
                if metadata.sensitive_frame_ids:
                    frames_to_remove = json.loads(metadata.sensitive_frame_ids)
                else:
                    return Response(
                        {'error': 'No analysis results available. Run analysis first.'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            except VideoMetadata.DoesNotExist:
                return Response(
                    {'error': 'Video not analyzed. Run analysis first.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        else:
            return Response(
                {'error': 'Must provide frame_list, frame_ranges, or detection_method=automatic'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if not frames_to_remove:
            return Response(
                {'error': 'No frames to remove'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Create processing history record
        history = VideoProcessingHistory.objects.create(
            video=video,
            operation=VideoProcessingHistory.OPERATION_FRAME_REMOVAL,
            status=VideoProcessingHistory.STATUS_PENDING,
            config={
                'frames_to_remove': frames_to_remove,
                'frame_count': len(frames_to_remove),
                'processing_method': processing_method
            }
        )
        
        try:
            history.mark_running()
            
            # Initialize FrameCleaner
            frame_cleaner = FrameCleaner()
            
            # Get video paths
            video_path = Path(video.raw_file.path) if hasattr(video.raw_file, 'path') else Path(str(video.raw_file))
            output_path = Path(settings.MEDIA_ROOT) / 'anonym_videos' / f"{video.uuid}_cleaned.mp4"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Remove frames (uses existing FrameCleaner.remove_frames_from_video)
            import time
            start_time = time.time()
            
            success = frame_cleaner.remove_frames_from_video(
                original_video=video_path,
                frames_to_remove=frames_to_remove,
                output_video=output_path
            )
            
            processing_time = time.time() - start_time
            
            if success:
                # Update video record
                update_processed_file(video, output_path)

                
                # Phase 1.4: Update LabelVideoSegments (shift frame numbers)
                segment_update_result = update_segments_after_frame_removal(video, frames_to_remove)
                
                # Mark history as success with segment update info
                details_parts = [
                    f"Removed {len(frames_to_remove)} frames in {processing_time:.1f}s"
                ]
                if segment_update_result['segments_updated'] > 0:
                    details_parts.append(
                        f"Updated {segment_update_result['segments_updated']} segments"
                    )
                if segment_update_result['segments_deleted'] > 0:
                    details_parts.append(
                        f"Deleted {segment_update_result['segments_deleted']} segments (all frames removed)"
                    )
                
                history.mark_success(
                    output_file=str(output_path),
                    details="; ".join(details_parts)
                )
                
                logger.info(f"Video {pk} cleaned: removed {len(frames_to_remove)} frames")
                
                return Response({
                    'task_id': None,  # Will be Celery task ID in Phase 1.2
                    'output_file': str(output_path),
                    'frames_removed': len(frames_to_remove),
                    'segment_updates': segment_update_result,
                    'message': 'Frame removal complete',
                    'processing_time': processing_time
                })
            else:
                raise Exception("Frame removal failed - check FFmpeg logs")
                
        except Exception as e:
            logger.error(f"Frame removal failed for {pk}: {str(e)}", exc_info=True)
            
            history.mark_failure(str(e))
            
            return Response(
                {'error': f'Frame removal failed: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def _parse_frame_ranges(self, ranges_str: str) -> list:
        """
        Parse frame ranges string to list of frame numbers.
        
        Example: "10-20,30,45-50" -> [10,11,...,20,30,45,...,50]
        """
        frames = []
        for part in ranges_str.split(','):
            part = part.strip()
            if '-' in part:
                start, end = map(int, part.split('-'))
                frames.extend(range(start, end + 1))
            else:
                frames.append(int(part))
        return sorted(set(frames))  # Remove duplicates and sort