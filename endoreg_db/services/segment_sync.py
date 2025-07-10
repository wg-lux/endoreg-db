"""
Service for synchronizing annotation updates with LabelVideoSegment creation.

This module provides functionality to create user-source LabelVideoSegments
when segment annotations are created or updated.
"""

import logging
from typing import Optional, Dict, Any
from django.contrib.auth.models import User
from django.db import transaction

from ..models import VideoFile, Label, LabelVideoSegment, InformationSource

logger = logging.getLogger(__name__)


def create_user_segment_from_annotation(annotation: Dict[str, Any], request_user: User) -> Optional[LabelVideoSegment]:
    """
    Create a user-source LabelVideoSegment from a segment annotation.
    
    This function:
    1. Locates the original LabelVideoSegment (if segmentId is present)
    2. Clones all its DB fields 
    3. Overwrites with new data from annotation
    4. Sets information_source = user
    5. Saves via model manager
    
    Args:
        annotation: Annotation data containing segment information
        request_user: The authenticated user making the request
        
    Returns:
        New LabelVideoSegment instance or None if creation failed/skipped
    """
    # Validate this is a segment annotation
    if annotation.get('type') != 'segment':
        logger.debug("Annotation is not a segment type, skipping user segment creation")
        return None
    
    # Get required data from annotation
    video_id = annotation.get('videoId')
    start_time = annotation.get('startTime')
    end_time = annotation.get('endTime')
    label_text = annotation.get('text', '').strip()
    metadata = annotation.get('metadata', {})
    original_segment_id = metadata.get('segmentId')
    
    if not all([video_id, start_time is not None, end_time is not None]):
        logger.warning("Missing required segment data in annotation, skipping user segment creation")
        return None
    
    try:
        # Get the video file
        video_file = VideoFile.objects.get(id=video_id)
        
        # Get video FPS for frame calculation
        fps = video_file.get_fps()
        if not fps or fps <= 0:
            logger.warning(f"Invalid FPS ({fps}) for video {video_id}, using default 25")
            fps = 25.0
        
        # Calculate frame numbers
        start_frame_number = int(start_time * fps)
        end_frame_number = int(end_time * fps)
        
        # Get or create user information source
        user_source, _ = InformationSource.objects.get_or_create(
            name="user",
            defaults={'description': 'User-generated annotations'}
        )
        
        # Try to find label by name from annotation text
        label = None
        if label_text:
            try:
                label = Label.objects.filter(name__iexact=label_text).first()
                if not label:
                    # Try to extract label from tags
                    tags = annotation.get('tags', [])
                    for tag in tags:
                        label = Label.objects.filter(name__iexact=tag).first()
                        if label:
                            break
            except Exception as e:
                logger.warning(f"Error finding label '{label_text}': {e}")
        
        # Start with default segment data
        segment_data = {
            'video_file': video_file,
            'start_frame_number': start_frame_number,
            'end_frame_number': end_frame_number,
            'source': user_source,
            'label': label,
            'prediction_meta': None,  # User segments don't have prediction meta
        }
        
        # If original segment ID is provided, try to clone from original
        if original_segment_id:
            try:
                original_segment = LabelVideoSegment.objects.get(id=original_segment_id)
                
                # Check if timing or label actually changed
                original_start_time = original_segment.start_frame_number / fps
                original_end_time = original_segment.end_frame_number / fps
                
                timing_changed = (
                    abs(original_start_time - start_time) > 0.1 or  # Allow small floating point differences
                    abs(original_end_time - end_time) > 0.1
                )
                
                label_changed = (
                    (label and original_segment.label != label) or
                    (not label and original_segment.label is not None)
                )
                
                # Only create new segment if something actually changed
                if not timing_changed and not label_changed:
                    logger.debug(f"No changes detected in segment {original_segment_id}, skipping user segment creation")
                    return None
                
                # Clone relevant fields from original segment
                segment_data.update({
                    'prediction_meta': original_segment.prediction_meta,
                    'label': label or original_segment.label,  # Use new label if provided, otherwise keep original
                })
                
                logger.info(f"Cloning segment {original_segment_id} with user modifications")
                
            except LabelVideoSegment.DoesNotExist:
                logger.warning(f"Original segment {original_segment_id} not found, creating new user segment")
        
        # Create the new user segment using the manager
        with transaction.atomic():
            new_segment = LabelVideoSegment.create_from_video(
                source=video_file,
                prediction_meta=segment_data.get('prediction_meta'),
                label=segment_data['label'],
                start_frame_number=start_frame_number,
                end_frame_number=end_frame_number,
            )
            
            # Set the user information source
            new_segment.source = user_source
            new_segment.save()
            
            logger.info(f"Created user segment {new_segment.id} for video {video_id} by user {request_user.username}")
            return new_segment
            
    except VideoFile.DoesNotExist:
        logger.error(f"Video {video_id} not found, cannot create user segment")
        return None
    except Exception as e:
        logger.error(f"Error creating user segment from annotation: {e}")
        return None