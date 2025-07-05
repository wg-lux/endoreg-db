"""
Video ingestion tasks using RQ (Redis Queue).

This module provides background task processing for video file imports,
allowing asynchronous processing of video files dropped into the inbox directory.
"""

import logging
import os
from pathlib import Path
from typing import Optional

import rq
import django
from django.conf import settings
from django.core.management import call_command
from redis import Redis

logger = logging.getLogger(__name__)

# Redis connection configuration
REDIS_URL = os.getenv('RQ_REDIS_URL', 'redis://localhost:6379/0')

def get_redis_connection():
    """Get Redis connection for RQ."""
    try:
        return Redis.from_url(REDIS_URL)
    except Exception as e:
        logger.error(f"Failed to connect to Redis at {REDIS_URL}: {e}")
        raise

def get_queue():
    """Get the 'anonymize' RQ queue."""
    redis_conn = get_redis_connection()
    return rq.Queue('anonymize', connection=redis_conn)

def run_import_video(file_path: str, center_name: Optional[str] = None, 
                    processor_name: Optional[str] = None) -> dict:
    """
    RQ task to import a video file using the video import service.
    
    Args:
        file_path: Path to the video file to import
        center_name: Optional center name (defaults to settings.DEFAULT_CENTER)
        processor_name: Optional processor name (defaults to settings.DEFAULT_PROCESSOR)
        
    Returns:
        Dict containing task result status and metadata
    """
    try:
        # Ensure Django is properly set up
        if not django.apps.apps.ready:
            django.setup()
        
        file_path = Path(file_path)
        if not file_path.exists():
            error_msg = f"Video file not found: {file_path}"
            logger.error(error_msg)
            return {
                'status': 'error',
                'message': error_msg,
                'file_path': str(file_path)
            }
        
        # Use settings defaults if not provided
        center_name = center_name or getattr(settings, 'DEFAULT_CENTER', 'university_hospital_wuerzburg')
        processor_name = processor_name or getattr(settings, 'DEFAULT_PROCESSOR', 'olympus_cv_1500')
        
        logger.info(f"Starting import for video: {file_path}")
        logger.info(f"Using center: {center_name}, processor: {processor_name}")
        
        # Try to use the service first
        try:
            from endoreg_db.services import video_import
            
            video_file_obj = video_import.import_and_anonymize(
                file_path=file_path,
                center_name=center_name,
                processor_name=processor_name,
                save_video=True,
                delete_source=True
            )
            
            success_msg = f"Successfully imported video: {file_path.name} (UUID: {video_file_obj.uuid})"
            logger.info(success_msg)
            
            return {
                'status': 'success',
                'message': success_msg,
                'file_path': str(file_path),
                'video_uuid': str(video_file_obj.uuid),
                'center_name': center_name,
                'processor_name': processor_name
            }
            
        except Exception as service_error:
            logger.warning(f"Service import failed: {service_error}, falling back to management command")
            
            # Fallback to Django management command
            call_command(
                "import_video", 
                str(file_path),
                "--center_name", center_name,
                "--processor_name", processor_name,
                "--save_video_file",
                "--delete_source"
            )
            
            success_msg = f"Successfully imported video via fallback: {file_path.name}"
            logger.info(success_msg)
            
            return {
                'status': 'success',
                'message': success_msg,
                'file_path': str(file_path),
                'center_name': center_name,
                'processor_name': processor_name,
                'method': 'fallback_command'
            }
        
    except Exception as e:
        error_msg = f"Failed to import video {file_path}: {str(e)}"
        logger.error(error_msg, exc_info=True)
        
        return {
            'status': 'error',
            'message': error_msg,
            'file_path': str(file_path),
            'exception': str(e)
        }

def enqueue_video_import(file_path: str, center_name: Optional[str] = None,
                        processor_name: Optional[str] = None) -> rq.Job:
    """
    Enqueue a video import task.
    
    Args:
        file_path: Path to the video file to import
        center_name: Optional center name
        processor_name: Optional processor name
        
    Returns:
        RQ Job instance
    """
    queue = get_queue()
    
    job = queue.enqueue(
        run_import_video,
        file_path,
        center_name,
        processor_name,
        job_timeout='30m',  # 30 minute timeout for video processing
        result_ttl=86400    # Keep results for 24 hours
    )
    
    logger.info(f"Enqueued video import job {job.id} for file: {file_path}")
    return job