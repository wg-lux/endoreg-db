"""
Video processing tasks for Celery-based asynchronous video processing.

This module provides Celery tasks for:
- Video masking with streaming FFmpeg processing
- Frame removal with streaming optimization
- Video reprocessing workflows
- Progress tracking and status updates
"""

from pathlib import Path
from typing import Dict, Any, List, Optional
from celery import shared_task
from celery.utils.log import get_task_logger
from django.shortcuts import get_object_or_404

logger = get_task_logger(__name__)

@shared_task(bind=True)
def apply_video_mask_task(self, video_id: int, mask_type: str = 'device_default', 
                         device_name: str = 'olympus_cv_1500', use_streaming: bool = True,
                         custom_mask: Optional[Dict[str, Any]] = None):
    """
    Apply mask to video using streaming processing.
    
    Args:
        video_id: ID of the VideoFile to process
        mask_type: Type of mask to apply ('device_default', 'roi_based', 'custom')
        device_name: Device name for default mask
        use_streaming: Whether to use streaming processing (recommended)
        custom_mask: Custom mask configuration (if mask_type='custom')
    """
    try:
        # Update task progress
        self.update_state(state='PROGRESS', meta={'progress': 0, 'message': 'Initializing mask application...'})
        
        # Import models here to avoid circular imports
        from endoreg_db.models import VideoFile
        from lx_anonymizer.frame_cleaner import FrameCleaner
        
        # Get video file
        video = get_object_or_404(VideoFile, pk=video_id)
        video_path = Path(video.file.path)
        
        if not video_path.exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")
        
        # Create output path
        output_dir = video_path.parent / "processed"
        output_dir.mkdir(exist_ok=True)
        output_path = output_dir / f"{video_path.stem}_masked{video_path.suffix}"
        
        self.update_state(state='PROGRESS', meta={'progress': 10, 'message': 'Setting up FrameCleaner...'})
        
        # Initialize FrameCleaner
        cleaner = FrameCleaner()
        
        # Determine mask configuration
        if mask_type == 'custom' and custom_mask:
            mask_config = custom_mask
        elif mask_type == 'roi_based':
            # Would need ROI data from video metadata
            mask_config = cleaner._load_mask(device_name)  # Fallback for now
        else:  # device_default
            mask_config = cleaner._load_mask(device_name)
        
        self.update_state(state='PROGRESS', meta={'progress': 20, 'message': f'Applying {mask_type} mask...'})
        
        # Apply mask using streaming
        if use_streaming:
            success = cleaner._mask_video_streaming(
                video_path, mask_config, output_path, use_named_pipe=True
            )
        else:
            success = cleaner._mask_video(video_path, mask_config, output_path)
        
        if not success:
            raise RuntimeError("Video masking failed")
        
        self.update_state(state='PROGRESS', meta={'progress': 90, 'message': 'Finalizing...'})
        
        # Verify output
        if not output_path.exists() or output_path.stat().st_size == 0:
            raise RuntimeError("Output video is empty or missing")
        
        result = {
            'status': 'completed',
            'output_path': str(output_path),
            'mask_type': mask_type,
            'device_name': device_name,
            'use_streaming': use_streaming,
            'output_size': output_path.stat().st_size,
            'message': 'Video masking completed successfully'
        }
        
        logger.info(f"Video masking completed for video {video_id}: {output_path}")
        return result
        
    except Exception as e:
        logger.error(f"Video masking failed for video {video_id}: {e}")
        self.update_state(
            state='FAILURE',
            meta={'error': str(e), 'message': f'Video masking failed: {e}'}
        )
        raise


def _setup_frame_removal(video_id: int, detection_engine: str):
    from endoreg_db.models import VideoFile
    from lx_anonymizer.frame_cleaner import FrameCleaner
    from django.shortcuts import get_object_or_404
    video = get_object_or_404(VideoFile, pk=video_id)
    video_path = Path(video.raw_file.path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")
    output_dir = video_path.parent / "processed"
    output_dir.mkdir(exist_ok=True)
    output_path = output_dir / f"{video_path.stem}_cleaned{video_path.suffix}"
    use_minicpm = detection_engine == 'minicpm'
    cleaner = FrameCleaner()
    return video, video_path, output_path, cleaner

def _detect_sensitive_frames(self, cleaner, video_path, selection_method, manual_frames, total_frames):
    frames_to_remove = []
    if selection_method == 'manual' and manual_frames:
        frames_to_remove = manual_frames
        self.update_state(state='PROGRESS', meta={'progress': 50, 'message': f'Using manual frame selection: {len(frames_to_remove)} frames'})
    elif selection_method == 'automatic':
        self.update_state(state='PROGRESS', meta={'progress': 20, 'message': 'Detecting sensitive frames...'})
        if total_frames > 10000:
            self.update_state(state='PROGRESS', meta={'progress': 30, 'message': 'Using statistical sampling for large video...'})
            sensitive_frames, estimated_ratio, early_stopped = cleaner._sample_frames_coroutine(
                video_path, total_frames, max_samples=500
            )
            frames_to_remove = sensitive_frames
        else:
            self.update_state(state='PROGRESS', meta={'progress': 30, 'message': 'Analyzing all frames...'})
            for abs_i, gray_frame, skip in cleaner._iter_video(video_path, total_frames):
                progress = 30 + (abs_i / total_frames) * 40  # 30-70% for detection
                if abs_i % 100 == 0:
                    self.update_state(state='PROGRESS', meta={
                        'progress': int(progress),
                        'message': f'Analyzing frame {abs_i}/{total_frames}...'
                    })
                ocr_text, avg_conf, _ = cleaner.frame_ocr.extract_text_from_frame(
                    gray_frame, roi=None, high_quality=True
                )
                if ocr_text:
                    frame_metadata = cleaner.extract_metadata_deepseek(ocr_text)
                    if not frame_metadata:
                        frame_metadata = cleaner.frame_metadata_extractor.extract_metadata_from_frame_text(ocr_text)
                    has_sensitive = cleaner.frame_metadata_extractor.is_sensitive_content(frame_metadata)
                    if has_sensitive:
                        frames_to_remove.append(abs_i)
    return frames_to_remove

def _remove_frames_from_video(cleaner, video_path, frames_to_remove, output_path, use_streaming):
    if use_streaming:
        return cleaner.remove_frames_from_video_streaming(
            video_path, frames_to_remove, output_path, use_named_pipe=True
        )
    else:
        return cleaner.remove_frames_from_video(
            video_path, frames_to_remove, output_path
        )

def _verify_frame_removal_output(output_path):
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError("Output video is empty or missing")

@shared_task(bind=True)
def remove_video_frames_task(self, video_id: int, selection_method: str = 'automatic',
                            detection_engine: str = 'minicpm', use_streaming: bool = True,
                            manual_frames: Optional[List[int]] = None):
    """
    Remove frames from video using streaming processing.
    
    Args:
        video_id: ID of the VideoFile to process
        selection_method: 'automatic' or 'manual'
        detection_engine: 'minicpm' or 'traditional'
        use_streaming: Whether to use streaming processing
        manual_frames: List of frame indices to remove (for manual selection)
    """
    try:
        # Update task progress
        self.update_state(state='PROGRESS', meta={'progress': 0, 'message': 'Initializing frame removal...'})
        
        video, video_path, output_path, cleaner = _setup_frame_removal(video_id, detection_engine)
        
        self.update_state(state='PROGRESS', meta={'progress': 10, 'message': 'Setting up FrameCleaner...'})
        
        # Get total frame count
        total_frames = cleaner._get_total_frames()
        
        frames_to_remove = _detect_sensitive_frames(
            self, cleaner, video_path, selection_method, manual_frames, total_frames
        )
        
        self.update_state(state='PROGRESS', meta={'progress': 70, 'message': f'Removing {len(frames_to_remove)} frames...'})
        
        # Remove frames using streaming
        success = _remove_frames_from_video(cleaner, video_path, frames_to_remove, output_path, use_streaming)

        if not success:
            raise RuntimeError("Frame removal failed")
        
        self.update_state(state='PROGRESS', meta={'progress': 90, 'message': 'Finalizing...'})
        
        _verify_frame_removal_output(output_path)
        
        result = {
            'status': 'completed',
            'output_path': str(output_path),
            'frames_removed': len(frames_to_remove),
            'selection_method': selection_method,
            'detection_engine': detection_engine,
            'use_streaming': use_streaming,
            'output_size': output_path.stat().st_size,
            'message': f'Successfully removed {len(frames_to_remove)} frames'
        }
        
        logger.info(f"Frame removal completed for video {video_id}: {len(frames_to_remove)} frames removed")
        return result
        
    except Exception as e:
        logger.error(f"Frame removal failed for video {video_id}: {e}")
        self.update_state(
            state='FAILURE',
            meta={'error': str(e), 'message': f'Frame removal failed: {e}'}
        )
        raise


@shared_task(bind=True)
def reprocess_video_task(self, video_id: int):
    """
    Reprocess video with updated settings.
    
    Args:
        video_id: ID of the VideoFile to reprocess
    """
    try:
        self.update_state(state='PROGRESS', meta={'progress': 0, 'message': 'Starting video reprocessing...'})
        
        # Import models here to avoid circular imports
        from endoreg_db.models import VideoFile
        from lx_anonymizer.frame_cleaner import FrameCleaner
        
        # Get video file
        video = get_object_or_404(VideoFile, pk=video_id)
        video_path = Path(video.file.path)
        
        if not video_path.exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")
        
        self.update_state(state='PROGRESS', meta={'progress': 20, 'message': 'Initializing FrameCleaner...'})
        
        # Initialize FrameCleaner with optimal settings
        cleaner = FrameCleaner()
        
        # Create output path
        output_dir = video_path.parent / "processed"
        output_dir.mkdir(exist_ok=True)
        output_path = output_dir / f"{video_path.stem}_reprocessed{video_path.suffix}"
        
        self.update_state(state='PROGRESS', meta={'progress': 30, 'message': 'Analyzing video...'})
        
        # Perform full analysis
        analysis_result = cleaner.analyze_video_sensitivity()
        
        self.update_state(state='PROGRESS', meta={'progress': 60, 'message': f'Processing with {analysis_result["recommended_method"]}...'})
        
        # Process based on analysis recommendation
        if analysis_result["recommended_method"] == "masking":
            # Apply device-specific masking
            mask_config = cleaner._load_mask("olympus_cv_1500")  # Default device
            success = cleaner._mask_video_streaming(
                video_path, mask_config, output_path, use_named_pipe=True
            )
        else:
            # Remove sensitive frames
            sensitive_frames = analysis_result.get("sensitive_frame_list", [])
            # Filter out string elements like "...truncated"
            sensitive_frames = [f for f in sensitive_frames if isinstance(f, int)]
            
            success = cleaner.remove_frames_from_video_streaming(
                video_path, sensitive_frames, output_path, use_named_pipe=True
            )
        
        if not success:
            raise RuntimeError("Video reprocessing failed")
        
        self.update_state(state='PROGRESS', meta={'progress': 90, 'message': 'Finalizing...'})
        
        # Update video metadata with analysis results
        if hasattr(video, 'sensitive_frame_count'):
            video.sensitive_frame_count = analysis_result.get('sensitive_frames', 0)
        if hasattr(video, 'total_frames'):
            video.total_frames = analysis_result.get('total_frames', 0)
        if hasattr(video, 'sensitive_ratio'):
            video.sensitive_ratio = analysis_result.get('sensitivity_ratio', 0.0)
        
        try:
            video.save()
        except Exception as e:
            logger.warning(f"Could not save video metadata: {e}")
        
        result = {
            'status': 'completed',
            'output_path': str(output_path),
            'analysis_result': analysis_result,
            'processing_method': analysis_result["recommended_method"],
            'output_size': output_path.stat().st_size if output_path.exists() else 0,
            'message': 'Video reprocessing completed successfully'
        }
        
        logger.info(f"Video reprocessing completed for video {video_id}")
        return result
        
    except Exception as e:
        logger.error(f"Video reprocessing failed for video {video_id}: {e}")
        self.update_state(
            state='FAILURE',
            meta={'error': str(e), 'message': f'Video reprocessing failed: {e}'}
        )
        raise