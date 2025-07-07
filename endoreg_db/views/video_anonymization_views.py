"""
API views for video anonymization and frame cleaning.
"""
import logging
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.conf import settings
from django.db import transaction

from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.media.video.video_file_anonymize import _anonymize
from endoreg_db.services.video_import import import_and_anonymize

logger = logging.getLogger(__name__)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def anonymize_video(request, video_id):
    """
    Start anonymization process for a specific video.
    
    POST /api/videos/{video_id}/anonymize/
    
    Body parameters:
    - delete_original_raw: bool (optional, default: True) - Whether to delete original raw files
    - force: bool (optional, default: False) - Force anonymization even if already processed
    
    Returns:
    - success: bool
    - message: str
    - video_id: str
    - anonymized: bool
    """
    try:
        video = get_object_or_404(VideoFile, uuid=video_id)
        
        # Parse request parameters
        delete_original_raw = request.data.get('delete_original_raw', True)
        force = request.data.get('force', False)
        
        # Check if already anonymized
        state = video.get_or_create_state()
        if state.anonymized and not force:
            return Response({
                'success': True,
                'message': 'Video is already anonymized',
                'video_id': str(video.uuid),
                'anonymized': True
            })
        
        # Validate prerequisites
        if not video.has_raw:
            return Response({
                'success': False,
                'message': 'Raw video file is missing',
                'video_id': str(video.uuid),
                'anonymized': False
            }, status=status.HTTP_400_BAD_REQUEST)
        
        if not state.frames_extracted:
            return Response({
                'success': False,
                'message': 'Video frames must be extracted first',
                'video_id': str(video.uuid),
                'anonymized': False
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Check sensitive metadata validation
        if not video.sensitive_meta or not video.sensitive_meta.is_verified:
            return Response({
                'success': False,
                'message': 'Sensitive metadata must be validated first',
                'video_id': str(video.uuid),
                'anonymized': False
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Check segment validation
        outside_segments = video.get_outside_segments(only_validated=False)
        unvalidated_outside = outside_segments.filter(state__is_validated=False)
        if unvalidated_outside.exists():
            return Response({
                'success': False,
                'message': f'All outside segments must be validated first. {unvalidated_outside.count()} segments pending validation.',
                'video_id': str(video.uuid),
                'anonymized': False
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Start anonymization process
        logger.info(f"Starting full anonymization pipeline for video {video.uuid} by user {request.user}")
        
        try:
            # Use the complete import_and_anonymize pipeline instead of just _anonymize
            # This ensures Pipe 1 (frame extraction, AI processing) and Pipe 2 (anonymization) are both executed
            
            # Get the video file path for re-processing
            video_file_path = video.get_raw_file_path()
            if not video_file_path or not video_file_path.exists():
                return Response({
                    'success': False,
                    'message': 'Raw video file path not accessible for re-processing',
                    'video_id': str(video.uuid),
                    'anonymized': False
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Get processor and center info from existing video
            processor_name = video.processor.name if video.processor else "olympus_cv_1500"
            center_name = video.center.name if video.center else "default_center"
            
            # Run the complete pipeline - this will:
            # 1. Re-extract frames if needed
            # 2. Run Pipe 1 (AI processing, metadata extraction)
            # 3. Run Pipe 2 (video anonymization)
            anonymized_video = import_and_anonymize(
                file_path=video_file_path,
                center_name=center_name,
                processor_name=processor_name,
                save_video=True,
                delete_source=False  # Don't delete the original during re-processing
            )
            
            if anonymized_video and anonymized_video.uuid == video.uuid:
                # Update the original video object reference
                video.refresh_from_db()
                
                return Response({
                    'success': True,
                    'message': 'Video anonymization pipeline completed successfully',
                    'video_id': str(video.uuid),
                    'anonymized': True
                })
            else:
                return Response({
                    'success': False,
                    'message': 'Video anonymization pipeline failed - video object mismatch',
                    'video_id': str(video.uuid),
                    'anonymized': False
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
                
        except Exception as pipeline_error:
            logger.error(f"Error in anonymization pipeline for video {video.uuid}: {pipeline_error}", exc_info=True)
            return Response({
                'success': False,
                'message': f'Anonymization pipeline failed: {str(pipeline_error)}',
                'video_id': str(video.uuid),
                'anonymized': False
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
    except ValueError as e:
        logger.error(f"Validation error during anonymization of video {video_id}: {e}")
        return Response({
            'success': False,
            'message': str(e),
            'video_id': video_id,
            'anonymized': False
        }, status=status.HTTP_400_BAD_REQUEST)
    
    except Exception as e:
        logger.error(f"Unexpected error during anonymization of video {video_id}: {e}", exc_info=True)
        return Response({
            'success': False,
            'message': f'Internal server error: {str(e)}',
            'video_id': video_id,
            'anonymized': False
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def anonymization_status(request, video_id):
    """
    Get anonymization status for a specific video.
    
    GET /api/videos/{video_id}/anonymization-status/
    
    Returns:
    - video_id: str
    - anonymized: bool
    - has_raw: bool
    - frames_extracted: bool
    - sensitive_meta_validated: bool
    - outside_segments_validated: bool
    - can_anonymize: bool
    - message: str
    """
    try:
        video = get_object_or_404(VideoFile, uuid=video_id)
        state = video.get_or_create_state()
        
        # Check prerequisites
        has_raw = video.has_raw
        frames_extracted = state.frames_extracted
        sensitive_meta_validated = bool(video.sensitive_meta and video.sensitive_meta.is_verified)
        
        outside_segments = video.get_outside_segments(only_validated=False)
        unvalidated_outside = outside_segments.filter(state__is_validated=False)
        outside_segments_validated = not unvalidated_outside.exists()
        
        can_anonymize = (
            has_raw and
            frames_extracted and
            sensitive_meta_validated and
            outside_segments_validated and
            not state.anonymized
        )
        
        # Generate status message
        if state.anonymized:
            message = "Video is already anonymized"
        elif not has_raw:
            message = "Raw video file is missing"
        elif not frames_extracted:
            message = "Video frames must be extracted first"
        elif not sensitive_meta_validated:
            message = "Sensitive metadata must be validated first"
        elif not outside_segments_validated:
            message = f"All outside segments must be validated first. {unvalidated_outside.count()} segments pending."
        elif can_anonymize:
            message = "Video is ready for anonymization"
        else:
            message = "Video cannot be anonymized - check prerequisites"
        
        return Response({
            'video_id': str(video.uuid),
            'anonymized': state.anonymized,
            'has_raw': has_raw,
            'frames_extracted': frames_extracted,
            'sensitive_meta_validated': sensitive_meta_validated,
            'outside_segments_validated': outside_segments_validated,
            'unvalidated_segments_count': unvalidated_outside.count(),
            'can_anonymize': can_anonymize,
            'message': message
        })
        
    except Exception as e:
        logger.error(f"Error getting anonymization status for video {video_id}: {e}", exc_info=True)
        return Response({
            'success': False,
            'message': f'Error getting status: {str(e)}',
            'video_id': video_id
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def anonymization_batch_status(request):
    """
    Get anonymization status for all videos or filtered videos.
    
    GET /api/videos/anonymization-batch-status/
    
    Query parameters:
    - ready_only: bool - Only return videos ready for anonymization
    - limit: int - Limit number of results
    
    Returns:
    - total_videos: int
    - anonymized_videos: int
    - ready_for_anonymization: int
    - videos: list of video status objects
    """
    try:
        # Get query parameters
        ready_only = request.GET.get('ready_only', 'false').lower() == 'true'
        limit = request.GET.get('limit')
        
        # Get all videos
        videos = VideoFile.objects.all()
        
        if limit:
            try:
                limit = int(limit)
                videos = videos[:limit]
            except ValueError:
                pass
        
        video_statuses = []
        anonymized_count = 0
        ready_count = 0
        
        for video in videos:
            state = video.get_or_create_state()
            
            # Check prerequisites
            has_raw = video.has_raw
            frames_extracted = state.frames_extracted
            sensitive_meta_validated = bool(video.sensitive_meta and video.sensitive_meta.is_verified)
            
            outside_segments = video.get_outside_segments(only_validated=False)
            unvalidated_outside = outside_segments.filter(state__is_validated=False)
            outside_segments_validated = not unvalidated_outside.exists()
            
            can_anonymize = (
                has_raw and
                frames_extracted and
                sensitive_meta_validated and
                outside_segments_validated and
                not state.anonymized
            )
            
            if state.anonymized:
                anonymized_count += 1
            
            if can_anonymize:
                ready_count += 1
            
            # Skip if only showing ready videos and this one isn't ready
            if ready_only and not can_anonymize:
                continue
            
            video_status = {
                'video_id': str(video.uuid),
                'anonymized': state.anonymized,
                'can_anonymize': can_anonymize,
                'has_raw': has_raw,
                'frames_extracted': frames_extracted,
                'sensitive_meta_validated': sensitive_meta_validated,
                'outside_segments_validated': outside_segments_validated,
                'unvalidated_segments_count': unvalidated_outside.count()
            }
            
            video_statuses.append(video_status)
        
        return Response({
            'total_videos': videos.count(),
            'anonymized_videos': anonymized_count,
            'ready_for_anonymization': ready_count,
            'videos': video_statuses
        })
        
    except Exception as e:
        logger.error(f"Error getting batch anonymization status: {e}", exc_info=True)
        return Response({
            'success': False,
            'message': f'Error getting batch status: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def anonymization_batch_start(request):
    """
    Start batch anonymization for multiple videos.
    
    POST /api/videos/anonymization-batch-start/
    
    Body parameters:
    - video_ids: list of video IDs (optional - if not provided, processes all ready videos)
    - delete_original_raw: bool (optional, default: True)
    - max_videos: int (optional) - Maximum number of videos to process
    
    Returns:
    - success: bool
    - message: str
    - processed_videos: list
    - failed_videos: list
    """
    try:
        # Parse request parameters
        video_ids = request.data.get('video_ids', [])
        delete_original_raw = request.data.get('delete_original_raw', True)
        max_videos = request.data.get('max_videos')
        
        if video_ids:
            # Process specific videos
            videos = VideoFile.objects.filter(uuid__in=video_ids)
        else:
            # Process all ready videos
            videos = VideoFile.objects.all()
        
        if max_videos:
            videos = videos[:max_videos]
        
        processed_videos = []
        failed_videos = []
        
        for video in videos:
            try:
                state = video.get_or_create_state()
                
                # Skip if already anonymized
                if state.anonymized:
                    continue
                
                # Check prerequisites
                if not video.has_raw:
                    failed_videos.append({
                        'video_id': str(video.uuid),
                        'error': 'Raw video file is missing'
                    })
                    continue
                
                if not state.frames_extracted:
                    failed_videos.append({
                        'video_id': str(video.uuid),
                        'error': 'Video frames must be extracted first'
                    })
                    continue
                
                if not video.sensitive_meta or not video.sensitive_meta.is_verified:
                    failed_videos.append({
                        'video_id': str(video.uuid),
                        'error': 'Sensitive metadata must be validated first'
                    })
                    continue
                
                outside_segments = video.get_outside_segments(only_validated=False)
                unvalidated_outside = outside_segments.filter(state__is_validated=False)
                if unvalidated_outside.exists():
                    failed_videos.append({
                        'video_id': str(video.uuid),
                        'error': f'All outside segments must be validated first. {unvalidated_outside.count()} segments pending.'
                    })
                    continue
                
                # Start anonymization
                logger.info(f"Batch anonymizing video {video.uuid}")
                success = _anonymize(video, delete_original_raw=delete_original_raw)
                
                if success:
                    processed_videos.append({
                        'video_id': str(video.uuid),
                        'status': 'success'
                    })
                else:
                    failed_videos.append({
                        'video_id': str(video.uuid),
                        'error': 'Anonymization process failed'
                    })
                
            except Exception as e:
                logger.error(f"Error during batch anonymization of video {video.uuid}: {e}")
                failed_videos.append({
                    'video_id': str(video.uuid),
                    'error': str(e)
                })
        
        success = len(failed_videos) == 0
        message = f"Batch anonymization completed. Processed: {len(processed_videos)}, Failed: {len(failed_videos)}"
        
        return Response({
            'success': success,
            'message': message,
            'processed_videos': processed_videos,
            'failed_videos': failed_videos,
            'total_processed': len(processed_videos),
            'total_failed': len(failed_videos)
        })
        
    except Exception as e:
        logger.error(f"Error during batch anonymization: {e}", exc_info=True)
        return Response({
            'success': False,
            'message': f'Batch anonymization error: {str(e)}',
            'processed_videos': [],
            'failed_videos': []
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)