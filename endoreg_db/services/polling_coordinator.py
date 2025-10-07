# endoreg_db/services/polling_coordinator.py

import logging
import threading
from typing import Dict, Optional
from django.core.cache import cache
from django.utils import timezone
from datetime import timedelta

logger = logging.getLogger(__name__)

class PollingCoordinator:
    """
    Service to prevent duplicate polling operations on the same media items.
    Uses Django cache and thread-safe operations to coordinate polling requests.
    """
    
    # Class-level lock for thread safety
    _lock = threading.Lock()
    
    # Cache key prefixes
    PROCESSING_PREFIX = "polling_processing:"
    LAST_CHECK_PREFIX = "polling_last_check:"
    
    # Default timeouts
    PROCESSING_TIMEOUT = 300  # 5 minutes
    CHECK_COOLDOWN = 10       # 10 seconds minimum between checks
    
    @classmethod
    def acquire_processing_lock(cls, file_id: int, file_type: str = "video", timeout: Optional[int] = None) -> bool:
        """
        Acquire a processing lock for a media file to prevent duplicate processing.
        
        Args:
            file_id: ID of the media file
            file_type: Type of media (video, pdf)
            timeout: Lock timeout in seconds (default: 5 minutes)
            
        Returns:
            True if lock acquired, False if already locked
        """
        if timeout is None:
            timeout = cls.PROCESSING_TIMEOUT
            
        cache_key = f"{cls.PROCESSING_PREFIX}{file_type}:{file_id}"
        
        with cls._lock:
            # Try to acquire lock atomically
            lock_acquired = cache.add(cache_key, {
                "locked_at": timezone.now().isoformat(),
                "file_id": file_id,
                "file_type": file_type,
                "thread_id": threading.get_ident()
            }, timeout)
            
            if lock_acquired:
                logger.info(f"Processing lock acquired for {file_type}:{file_id}")
                return True
            else:
                # Check if existing lock is stale
                existing_lock = cache.get(cache_key)
                if existing_lock:
                    logger.warning(f"Processing lock already exists for {file_type}:{file_id}: {existing_lock}")
                else:
                    logger.warning(f"Failed to acquire processing lock for {file_type}:{file_id}")
                return False
    
    @classmethod
    def release_processing_lock(cls, file_id: int, file_type: str = "video") -> bool:
        """
        Release a processing lock for a media file.
        
        Args:
            file_id: ID of the media file
            file_type: Type of media (video, pdf)
            
        Returns:
            True if lock released, False if lock didn't exist
        """
        cache_key = f"{cls.PROCESSING_PREFIX}{file_type}:{file_id}"
        
        with cls._lock:
            if cache.delete(cache_key):
                logger.info(f"Processing lock released for {file_type}:{file_id}")
                return True
            else:
                logger.warning(f"No processing lock found to release for {file_type}:{file_id}")
                return False
    
    @classmethod
    def is_processing_locked(cls, file_id: int, file_type: str = "video") -> bool:
        """
        Check if a media file is currently processing locked.
        
        Args:
            file_id: ID of the media file
            file_type: Type of media (video, pdf)
            
        Returns:
            True if locked, False otherwise
        """
        cache_key = f"{cls.PROCESSING_PREFIX}{file_type}:{file_id}"
        return cache.get(cache_key) is not None
    
    @classmethod
    def can_check_status(cls, file_id: int, file_type: str = "video") -> bool:
        """
        Check if enough time has passed since last status check to prevent spam.
        
        Args:
            file_id: ID of the media file
            file_type: Type of media (video, pdf)
            
        Returns:
            True if status check is allowed, False if still in cooldown
        """
        cache_key = f"{cls.LAST_CHECK_PREFIX}{file_type}:{file_id}"
        last_check = cache.get(cache_key)
        
        if last_check is None:
            # First check or cooldown expired - allowed
            cls._record_status_check(file_id, file_type)
            return True
        
        # Check if cooldown period has passed
        last_check_time = timezone.datetime.fromisoformat(last_check.replace('Z', '+00:00'))
        cooldown_end = last_check_time + timedelta(seconds=cls.CHECK_COOLDOWN)
        
        if timezone.now() > cooldown_end:
            cls._record_status_check(file_id, file_type)
            return True
        else:
            remaining_cooldown = (cooldown_end - timezone.now()).total_seconds()
            logger.debug(f"Status check cooldown active for {file_type}:{file_id}, {remaining_cooldown:.1f}s remaining")
            return False
    
    @classmethod
    def get_remaining_cooldown_seconds(cls, file_id: int, file_type: str = "video") -> int:
        """
        Get the remaining cooldown seconds for a status check.
        
        Args:
            file_id: ID of the media file
            file_type: Type of media (video, pdf)
            
        Returns:
            Remaining cooldown in seconds (0 if no cooldown active)
        """
        cache_key = f"{cls.LAST_CHECK_PREFIX}{file_type}:{file_id}"
        last_check = cache.get(cache_key)
        
        if last_check is None:
            return 0
        
        # Check if cooldown period has passed
        last_check_time = timezone.datetime.fromisoformat(last_check.replace('Z', '+00:00'))
        cooldown_end = last_check_time + timedelta(seconds=cls.CHECK_COOLDOWN)
        
        if timezone.now() > cooldown_end:
            return 0
        else:
            remaining_cooldown = (cooldown_end - timezone.now()).total_seconds()
            # Round up to at least 1 second
            return max(1, int(remaining_cooldown) + 1)

    @classmethod
    def _record_status_check(cls, file_id: int, file_type: str = "video") -> None:
        """Record the time of a status check"""
        cache_key = f"{cls.LAST_CHECK_PREFIX}{file_type}:{file_id}"
        cache.set(cache_key, timezone.now().isoformat(), cls.CHECK_COOLDOWN + 5)
    
    @classmethod
    def get_processing_locks_info(cls) -> Dict[str, any]:
        """
        Get information about all currently active processing locks.
        Useful for debugging and monitoring.
        
        Returns:
            Dictionary with lock information
        """
        # Note: This is a simplified version since Django cache doesn't support pattern scanning
        # In production, consider using Redis with SCAN command for better performance
        
        info = {
            "coordinator_status": "active",
            "config": {
                "processing_timeout": cls.PROCESSING_TIMEOUT,
                "check_cooldown": cls.CHECK_COOLDOWN
            },
            "note": "Active locks info requires Redis backend for pattern scanning"
        }
        
        return info
    
    @classmethod
    def clear_all_locks(cls, file_type: Optional[str] = None) -> int:
        """
        Emergency function to clear all processing locks.
        Use with caution - only for debugging/recovery scenarios.
        
        Args:
            file_type: Optionally clear locks only for specific file type
            
        Returns:
            Number of locks cleared (approximation)
        """
        logger.warning("clear_all_locks called - this should only be used for debugging/recovery")
        
        # This is a simplified implementation
        # In production with Redis, you'd use SCAN to find and delete matching keys
        if hasattr(cache, 'clear'):
            cache.clear()
            return -1  # Unknown count
        else:
            logger.warning("Cache backend doesn't support pattern deletion")
            return 0


# Decorator for views that need processing coordination
def processing_coordination(file_id_param: str = "file_id", file_type: str = "video"):
    """
    Decorator to add automatic processing coordination to views.
    
    Args:
        file_id_param: Name of the parameter containing file ID
        file_type: Type of media file
    """
    def decorator(view_func):
        def wrapper(request, *args, **kwargs):
            # Extract file_id from kwargs or request
            file_id = kwargs.get(file_id_param) or request.data.get(file_id_param)
            
            if file_id is None:
                logger.error(f"No {file_id_param} found in request for processing coordination")
                from rest_framework.response import Response
                from rest_framework import status
                return Response(
                    {"error": f"Missing {file_id_param} parameter"}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Check if processing is already locked
            if PollingCoordinator.is_processing_locked(file_id, file_type):
                from rest_framework.response import Response
                from rest_framework import status
                return Response(
                    {"detail": "File is currently being processed by another request"}, 
                    status=status.HTTP_409_CONFLICT
                )
            
            # Proceed with the view
            return view_func(request, *args, **kwargs)
        
        return wrapper
    return decorator


# Context manager for automatic lock management
class ProcessingLockContext:
    """
    Context manager for automatic processing lock acquisition and release.
    
    Usage:
        with ProcessingLockContext(file_id, "video") as lock:
            if lock.acquired:
                # Perform processing
                pass
            else:
                # Handle lock acquisition failure
                pass
    """
    
    def __init__(self, file_id: int, file_type: str = "video", timeout: Optional[int] = None):
        self.file_id = file_id
        self.file_type = file_type
        self.timeout = timeout
        self.acquired = False
    
    def __enter__(self):
        self.acquired = PollingCoordinator.acquire_processing_lock(
            self.file_id, self.file_type, self.timeout
        )
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.acquired:
            PollingCoordinator.release_processing_lock(self.file_id, self.file_type)
        return False  # Don't suppress exceptions
