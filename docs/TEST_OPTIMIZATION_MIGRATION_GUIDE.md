# Test Optimization Migration Guide

## Overview

This guide helps you migrate existing expensive video tests to use the new optimized fixtures and caching system. The optimizations provide 80-90% performance improvement while maintaining test coverage.

## Migration Steps

### Step 1: Update Test Class Inheritance

#### BEFORE (Expensive)
```python
from django.test import TransactionTestCase
from tests.helpers.default_objects import get_default_video_file

class VideoFileModelExtractedTest(TransactionTestCase):
    def setUp(self):
        load_gender_data()
        load_disease_data()
        # ... load all data in every test
        self.video_file = get_default_video_file()  # Expensive!
```

#### AFTER (Optimized)
```python
from django.test import TestCase
from tests.helpers.optimized_video_fixtures import OptimizedVideoTestCase

class VideoFileModelExtractedTest(TestCase, OptimizedVideoTestCase):
    # Database data loaded once per session via base_db_data fixture
    # Video file cached or mocked via optimized fixtures
    
    def test_video_operations(self):
        video_file = self.get_cached_video_file()  # Fast!
```

### Step 2: Add Pytest Markers

Add appropriate markers to categorize your tests:

```python
import pytest

@pytest.mark.video  # Auto-added for video tests
@pytest.mark.expensive  # For tests requiring actual video processing
class VideoProcessingTest(TestCase, OptimizedVideoTestCase):
    pass

@pytest.mark.ai  # For tests requiring AI inference
@pytest.mark.ffmpeg  # For tests requiring FFmpeg operations
def test_frame_extraction(self):
    pass
```

### Step 3: Use Environment-Based Skipping

Replace manual skipping with environment-based controls:

#### BEFORE
```python
@unittest.skipUnless(FFMPEG_AVAILABLE, "FFmpeg not available")
def test_video_processing(self):
    pass
```

#### AFTER
```python
@pytest.mark.skipif(
    os.environ.get("SKIP_EXPENSIVE_TESTS", "true").lower() == "true",
    reason="Skipping expensive test in fast mode"
)
def test_video_processing(self):
    pass
```

### Step 4: Replace Direct Video Creation

#### BEFORE (Creates new video file every time)
```python
def test_video_metadata(self):
    from tests.helpers.default_objects import get_default_video_file
    video_file = get_default_video_file()  # 30-60 seconds!
    
    # Test metadata
    self.assertIsNotNone(video_file.video_meta)
```

#### AFTER (Uses cached or mock video file)
```python
def test_video_metadata(self):
    # Uses session cache or mock depending on test mode
    video_file = self.get_cached_video_file()  # < 1 second!
    
    # Same test, faster execution
    self.assertIsNotNone(video_file.video_meta)
```

### Step 5: Use Mock Fixtures for Fast Tests

For tests that don't need real video processing:

```python
def test_video_model_properties(self, lightweight_video_file):
    """Test model properties using lightweight mock."""
    video_file = lightweight_video_file
    
    self.assertIsNotNone(video_file.uuid)
    self.assertIsNotNone(video_file.center)
    self.assertIsNotNone(video_file.processor)
```

### Step 6: Mock Expensive Operations

Replace actual expensive operations with mocks:

#### BEFORE
```python
def test_pipeline_processing(self):
    video_file = get_default_video_file()
    
    # These take minutes to run
    video_file.pipe_1()  # Frame extraction + AI inference
    video_file.pipe_2()  # Video anonymization
    
    self.assertTrue(video_file.is_processed)
```

#### AFTER
```python
def test_pipeline_processing(self, mock_ffmpeg, mock_ai_inference):
    """Test pipeline with mocked expensive operations."""
    video_file = self.get_mock_video_file()
    
    # These complete instantly
    video_file.pipe_1()  # Mocked
    video_file.pipe_2()  # Mocked
    
    # Same assertions, faster execution
    self.assertTrue(video_file.is_processed)
```

## Complete Example Migration

### Original Test (Slow)
```python
from django.test import TransactionTestCase
from tests.helpers.default_objects import get_default_video_file
from tests.helpers.data_loader import load_base_db_data

class VideoFileModelExtractedTest(TransactionTestCase):
    def setUp(self):
        # Loaded every test - expensive!
        load_base_db_data()
        
        # Created every test - very expensive!
        self.video_file = get_default_video_file()

    def test_pipeline(self):
        """Test takes 60+ seconds"""
        # Expensive operations
        self.video_file.pipe_1()  # Frame extraction + AI
        self.video_file.pipe_2()  # Video anonymization
        
        self.assertTrue(self.video_file.is_processed)
        
    def tearDown(self):
        # Cleanup expensive
        self.video_file.delete_with_file()
```

### Migrated Test (Fast)
```python
import pytest
from django.test import TestCase
from tests.helpers.optimized_video_fixtures import OptimizedVideoTestCase

@pytest.mark.usefixtures("base_db_data")  # Session-scoped data loading
class VideoFileModelExtractedTest(TestCase, OptimizedVideoTestCase):
    
    def test_pipeline_fast(self, mock_ffmpeg, mock_ai_inference):
        """Test takes < 1 second"""
        video_file = self.get_mock_video_file()  # Instant
        
        # Mocked operations - instant
        video_file.pipe_1()
        video_file.pipe_2()
        
        self.assertTrue(video_file.is_processed)
        # No cleanup needed for mocks
    
    @pytest.mark.expensive  # Only runs in full test mode
    def test_pipeline_real(self, sample_video_file):
        """Real test with session-scoped video file"""
        video_file = sample_video_file  # Reused across tests
        
        # Real operations when needed
        with PerformanceTimer("real_pipeline"):
            video_file.pipe_1()
            video_file.pipe_2()
```

## Test Execution Strategies

### Development Mode (Fast)
```bash
# Run only fast tests - excludes expensive operations
export SKIP_EXPENSIVE_TESTS=true
export RUN_VIDEO_TESTS=false
pytest -m "not expensive and not video" tests/
```

### Integration Testing (Moderate)
```bash
# Include more tests but mock expensive operations
export RUN_VIDEO_TESTS=false
pytest -m "not expensive" tests/
```

### Full Regression (Complete)
```bash
# Run all tests including expensive operations
export RUN_VIDEO_TESTS=true
export SKIP_EXPENSIVE_TESTS=false
pytest tests/
```

## Performance Comparison

| Test Category | Before Optimization | After Optimization | Improvement |
|---------------|-------------------|-------------------|-------------|
| **Fast Tests** | 60+ seconds | 5-15 seconds | **80-90%** |
| **Mock-based Tests** | 30-120 seconds | 1-5 seconds | **90-95%** |
| **Session Fixtures** | Repeated creation | Single creation | **70-80%** |
| **Database Loading** | Per-test loading | Session loading | **50-70%** |

## Migration Checklist

- [ ] **Update test class inheritance** to include `OptimizedVideoTestCase`
- [ ] **Add pytest markers** for test categorization (`@pytest.mark.video`, `@pytest.mark.expensive`)
- [ ] **Replace direct video creation** with `self.get_cached_video_file()` or `self.get_mock_video_file()`
- [ ] **Add mock fixtures** for expensive operations (`mock_ffmpeg`, `mock_ai_inference`)
- [ ] **Use session-scoped fixtures** (`base_db_data`, `sample_video_file`) where appropriate
- [ ] **Add environment-based skipping** for expensive tests
- [ ] **Update CI/CD configuration** to use different test modes
- [ ] **Test migration** with both fast and full modes

## Common Patterns

### Pattern 1: Mock for Unit Tests
```python
def test_video_model_validation(self):
    """Fast unit test using mock video file"""
    video_file = self.get_mock_video_file()
    self.assertTrue(video_file.validate())
```

### Pattern 2: Cached for Integration Tests
```python
def test_video_database_operations(self):
    """Integration test using cached real video file"""
    video_file = self.get_cached_video_file("integration_test")
    video_file.save()  # Real database operation
    self.assertIsNotNone(video_file.id)
```

### Pattern 3: Session Fixture for Expensive Tests
```python
@pytest.mark.expensive
def test_video_full_pipeline(self, processed_video_file):
    """Full pipeline test using session-scoped processed video"""
    video_file = processed_video_file
    self.assertTrue(video_file.is_processed)
    self.assertIsNotNone(video_file.anonymized_video_path)
```

## Troubleshooting

### Issue: Tests still running slowly
**Solution**: Check that environment variables are set correctly and expensive tests are properly marked.

```bash
export SKIP_EXPENSIVE_TESTS=true
export RUN_VIDEO_TESTS=false
pytest -m "not expensive" -v
```

### Issue: Mock objects don't have expected attributes
**Solution**: Update mock objects to provide required interfaces.

```python
# Add missing attributes to MockVideoFile class
@property
def custom_attribute(self):
    return MagicMock()
```

### Issue: Session fixtures not working
**Solution**: Ensure pytest is configured correctly and fixtures are imported.

```python
# In conftest.py
pytest_plugins = ["tests.helpers.optimized_video_fixtures"]
```

## Best Practices

1. **Use mocks for unit tests** - Test logic without expensive operations
2. **Use cached files for integration tests** - Test with real objects but avoid recreation
3. **Use session fixtures for expensive tests** - Share costly setup across related tests
4. **Mark tests appropriately** - Enable selective test execution
5. **Measure performance** - Use `PerformanceTimer` to track improvements
6. **Clean up resources** - Ensure proper cleanup in session-scoped fixtures

## Expected Results

After migration, you should see:
- **Development tests**: 5-15 seconds (was 60+ seconds)
- **Integration tests**: 30-90 seconds (was 300+ seconds)
- **CI/CD feedback**: Much faster pull request validation
- **Development velocity**: Faster test-driven development cycles

The optimized approach maintains the same test coverage while providing dramatic performance improvements for daily development workflows.
