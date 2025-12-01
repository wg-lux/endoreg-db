"""
Comprehensive unit tests for PollingCoordinator service.

Tests cover:
- Processing lock acquisition and release
- Thread-safe operations
- Status check cooldown mechanism
- Lock timeout and expiration
- Context manager usage
"""
import pytest
import time
import threading
from unittest.mock import patch, MagicMock
from django.core.cache import cache
from django.utils import timezone
from datetime import timedelta
from endoreg_db.services.polling_coordinator import (
    PollingCoordinator,
    ProcessingLockContext
)


@pytest.mark.django_db
class TestPollingCoordinator:
    """Test suite for PollingCoordinator service."""
    
    def setup_method(self):
        """Clear cache before each test."""
        cache.clear()
    
    def teardown_method(self):
        """Clear cache after each test."""
        cache.clear()
    
    def test_acquire_processing_lock_success(self):
        """Test successful lock acquisition."""
        file_id = 123
        file_type = "video"
        
        result = PollingCoordinator.acquire_processing_lock(file_id, file_type)
        
        assert result is True
        assert PollingCoordinator.is_processing_locked(file_id, file_type)
    
    def test_acquire_processing_lock_already_locked(self):
        """Test lock acquisition fails when already locked."""
        file_id = 456
        file_type = "video"
        
        # First acquisition should succeed
        result1 = PollingCoordinator.acquire_processing_lock(file_id, file_type)
        assert result1 is True
        
        # Second acquisition should fail
        result2 = PollingCoordinator.acquire_processing_lock(file_id, file_type)
        assert result2 is False
    
    def test_acquire_processing_lock_different_files(self):
        """Test acquiring locks for different files."""
        file_id1 = 100
        file_id2 = 200
        file_type = "video"
        
        result1 = PollingCoordinator.acquire_processing_lock(file_id1, file_type)
        result2 = PollingCoordinator.acquire_processing_lock(file_id2, file_type)
        
        assert result1 is True
        assert result2 is True
    
    def test_acquire_processing_lock_different_types(self):
        """Test acquiring locks for same ID but different types."""
        file_id = 300
        
        result_video = PollingCoordinator.acquire_processing_lock(file_id, "video")
        result_pdf = PollingCoordinator.acquire_processing_lock(file_id, "pdf")
        
        assert result_video is True
        assert result_pdf is True
    
    def test_acquire_processing_lock_custom_timeout(self):
        """Test lock acquisition with custom timeout."""
        file_id = 400
        file_type = "video"
        custom_timeout = 60
        
        result = PollingCoordinator.acquire_processing_lock(
            file_id, file_type, timeout=custom_timeout
        )
        
        assert result is True
    
    def test_release_processing_lock_success(self):
        """Test successful lock release."""
        file_id = 500
        file_type = "video"
        
        PollingCoordinator.acquire_processing_lock(file_id, file_type)
        assert PollingCoordinator.is_processing_locked(file_id, file_type)
        
        result = PollingCoordinator.release_processing_lock(file_id, file_type)
        
        assert result is True
        assert not PollingCoordinator.is_processing_locked(file_id, file_type)
    
    def test_release_processing_lock_not_exists(self):
        """Test releasing non-existent lock."""
        file_id = 600
        file_type = "video"
        
        result = PollingCoordinator.release_processing_lock(file_id, file_type)
        
        assert result is False
    
    def test_is_processing_locked_true(self):
        """Test checking if file is locked."""
        file_id = 700
        file_type = "video"
        
        PollingCoordinator.acquire_processing_lock(file_id, file_type)
        
        assert PollingCoordinator.is_processing_locked(file_id, file_type) is True
    
    def test_is_processing_locked_false(self):
        """Test checking if file is not locked."""
        file_id = 800
        file_type = "video"
        
        assert PollingCoordinator.is_processing_locked(file_id, file_type) is False
    
    def test_can_check_status_first_time(self):
        """Test status check allowed on first attempt."""
        file_id = 900
        file_type = "video"
        
        result = PollingCoordinator.can_check_status(file_id, file_type)
        
        assert result is True
    
    def test_can_check_status_cooldown_active(self):
        """Test status check blocked during cooldown."""
        file_id = 1000
        file_type = "video"
        
        # First check
        result1 = PollingCoordinator.can_check_status(file_id, file_type)
        assert result1 is True
        
        # Immediate second check should be blocked
        result2 = PollingCoordinator.can_check_status(file_id, file_type)
        assert result2 is False
    
    def test_can_check_status_after_cooldown(self):
        """Test status check allowed after cooldown expires."""
        file_id = 1100
        file_type = "video"
        
        # Set a very short cooldown for testing
        original_cooldown = PollingCoordinator.CHECK_COOLDOWN
        PollingCoordinator.CHECK_COOLDOWN = 1  # 1 second
        
        try:
            # First check
            result1 = PollingCoordinator.can_check_status(file_id, file_type)
            assert result1 is True
            
            # Wait for cooldown to expire
            time.sleep(1.1)
            
            # Second check should now be allowed
            result2 = PollingCoordinator.can_check_status(file_id, file_type)
            assert result2 is True
        finally:
            # Restore original cooldown
            PollingCoordinator.CHECK_COOLDOWN = original_cooldown
    
    def test_get_remaining_cooldown_seconds_active(self):
        """Test getting remaining cooldown seconds."""
        file_id = 1200
        file_type = "video"
        
        # Trigger a status check
        PollingCoordinator.can_check_status(file_id, file_type)
        
        # Check remaining cooldown
        remaining = PollingCoordinator.get_remaining_cooldown_seconds(file_id, file_type)
        
        assert remaining > 0
        assert remaining <= PollingCoordinator.CHECK_COOLDOWN
    
    def test_get_remaining_cooldown_seconds_expired(self):
        """Test getting remaining cooldown when expired."""
        file_id = 1300
        file_type = "video"
        
        remaining = PollingCoordinator.get_remaining_cooldown_seconds(file_id, file_type)
        
        assert remaining == 0
    
    def test_thread_safety(self):
        """Test thread-safe lock acquisition."""
        file_id = 1400
        file_type = "video"
        results = []
        
        def try_acquire():
            result = PollingCoordinator.acquire_processing_lock(file_id, file_type)
            results.append(result)
        
        # Create multiple threads trying to acquire same lock
        threads = [threading.Thread(target=try_acquire) for _ in range(10)]
        
        for thread in threads:
            thread.start()
        
        for thread in threads:
            thread.join()
        
        # Only one thread should succeed
        assert sum(results) == 1
        assert results.count(True) == 1
        assert results.count(False) == 9
    
    def test_get_processing_locks_info(self):
        """Test getting locks information."""
        info = PollingCoordinator.get_processing_locks_info()
        
        assert 'coordinator_status' in info
        assert info['coordinator_status'] == 'active'
        assert 'config' in info
        assert 'processing_timeout' in info['config']
        assert 'check_cooldown' in info['config']


@pytest.mark.django_db
class TestProcessingLockContext:
    """Test suite for ProcessingLockContext manager."""
    
    def setup_method(self):
        """Clear cache before each test."""
        cache.clear()
    
    def teardown_method(self):
        """Clear cache after each test."""
        cache.clear()
    
    def test_context_manager_acquire_and_release(self):
        """Test context manager acquires and releases lock."""
        file_id = 2000
        file_type = "video"
        
        assert not PollingCoordinator.is_processing_locked(file_id, file_type)
        
        with ProcessingLockContext(file_id, file_type) as lock:
            assert lock.acquired is True
            assert PollingCoordinator.is_processing_locked(file_id, file_type)
        
        # Lock should be released after exiting context
        assert not PollingCoordinator.is_processing_locked(file_id, file_type)
    
    def test_context_manager_acquisition_failure(self):
        """Test context manager when lock acquisition fails."""
        file_id = 2100
        file_type = "video"
        
        # Acquire lock outside context
        PollingCoordinator.acquire_processing_lock(file_id, file_type)
        
        # Context manager should fail to acquire
        with ProcessingLockContext(file_id, file_type) as lock:
            assert lock.acquired is False
        
        # Original lock should still be held
        assert PollingCoordinator.is_processing_locked(file_id, file_type)
    
    def test_context_manager_with_exception(self):
        """Test context manager releases lock even with exception."""
        file_id = 2200
        file_type = "video"
        
        def _raise_error():
            raise ValueError
        
        try:
            with ProcessingLockContext(file_id, file_type) as lock:
                assert lock.acquired is True
                _raise_error()
        except ValueError:
            pass
        
        # Lock should be released despite exception
        assert not PollingCoordinator.is_processing_locked(file_id, file_type)
    
    def test_context_manager_custom_timeout(self):
        """Test context manager with custom timeout."""
        file_id = 2300
        file_type = "video"
        custom_timeout = 120
        
        with ProcessingLockContext(file_id, file_type, timeout=custom_timeout) as lock:
            assert lock.acquired is True
    
    def test_context_manager_attributes(self):
        """Test context manager attributes."""
        file_id = 2400
        file_type = "pdf"
        
        with ProcessingLockContext(file_id, file_type) as lock:
            assert lock.file_id == file_id
            assert lock.file_type == file_type
            assert lock.acquired is True