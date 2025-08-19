# Test Performance Optimization Guide

## Executive Summary

The test suite contains significant performance bottlenecks primarily related to video processing operations. Multiple tests are performing expensive operations like:
- Video transcoding and format conversion
- Frame extraction from videos  
- AI model inference/prediction
- File I/O operations with large video files
- Full pipeline processing (Pipe 1 & Pipe 2)

## Current State Analysis

### Performance-Heavy Test Files Identified

#### High Impact (Major video processing)
1. **`tests/media/video/test_video_file_extracted.py`**
   - Runs complete video pipeline: Pipe 1 → Mock Validation → Pipe 2
   - Frame extraction, AI inference, anonymization
   - **Estimated impact: HIGH** (full pipeline)

2. **`tests/test_video_pipeline.py`**
   - Complete demonstration pipeline
   - **Estimated impact: HIGH** (full pipeline)

3. **`tests/test_video_import_service.py`**
   - Video import + anonymization service
   - **Estimated impact: HIGH** (full pipeline)

4. **`tests/pipelines/test_process_video_dir.py`**
   - Bulk video directory processing
   - **Estimated impact: CRITICAL** (multiple videos)

#### Medium Impact (Partial video processing)
5. **`tests/media/video/test_video_file.py`**
   - Video file creation and basic operations
   - **Estimated impact: MEDIUM** (video metadata, specs)

6. **`tests/label/test_label_video_segment.py`**
   - Frame extraction for segments
   - **Estimated impact: MEDIUM** (selective frame extraction)

#### Supporting Files (Infrastructure)
- `tests/media/video/test_pipe_1.py` - Pipeline stage 1
- `tests/media/video/test_pipe_2.py` - Pipeline stage 2  
- `tests/media/video/helper.py` - Video file utilities
- `tests/helpers/default_objects.py` - Test object creation

### Root Causes of Performance Issues

#### 1. Repeated Video Initialization
```python
# In get_default_video_file():
video_file = VideoFile.create_from_file_initialized(
    file_path=video_path,
    center_name=DEFAULT_CENTER_NAME,
    delete_source=False,  # Files are recreated every time
    processor_name=DEFAULT_ENDOSCOPY_PROCESSOR_NAME,
)
```

**Problem**: Each test creates a new VideoFile from scratch, involving:
- File copying/moving operations
- Video metadata extraction (`initialize_video_specs()`)
- Frame directory setup
- Database record creation
- State initialization

#### 2. Expensive Pipeline Operations
```python
# In test_video_file_extracted.py:
def test_pipeline(self):
    _test_pipe_1(self)      # Frame extraction + AI inference
    mock_video_anonym_annotation(self)  # Mock validation
    _test_pipe_2(self)      # Video anonymization + cleanup
```

**Pipeline Stage Costs**:
- **Pipe 1**: Frame extraction (FFmpeg), OCR processing, AI model inference, label creation
- **Pipe 2**: Video anonymization (FFmpeg), file cleanup, processed file generation

#### 3. No Test Isolation/Caching
- Tests don't reuse video files or processing results
- No fixture caching for expensive operations
- Each test suite runs independently

#### 4. Redundant File Operations
```python
# Tests repeatedly:
load_base_db_data()  # Database fixtures
get_random_video_path_by_examination_alias()  # File selection
VideoFile.create_from_file_initialized()  # File processing
```

## Optimization Strategies

### Strategy 1: Conditional Test Execution 🎯
**Impact: HIGH | Effort: LOW**

#### Current Implementation
```python
RUN_VIDEO_TESTS = os.environ.get("RUN_VIDEO_TESTS", "true").lower() == "true"

@unittest.skipUnless(FFMPEG_AVAILABLE, "FFmpeg command not found, skipping frame extraction test.")
```

#### Recommended Enhancement
```python
# Add more granular control
RUN_VIDEO_TESTS = os.environ.get("RUN_VIDEO_TESTS", "false").lower() == "true"  # Default to false
RUN_PIPELINE_TESTS = os.environ.get("RUN_PIPELINE_TESTS", "false").lower() == "true"
RUN_AI_INFERENCE_TESTS = os.environ.get("RUN_AI_INFERENCE_TESTS", "false").lower() == "true"
SKIP_EXPENSIVE_TESTS = os.environ.get("SKIP_EXPENSIVE_TESTS", "true").lower() == "true"
```

#### Implementation
```python
# In pytest.ini or conftest.py
@pytest.fixture(scope="session", autouse=True)
def configure_test_performance():
    """Configure performance-related test settings."""
    if os.environ.get("FAST_TESTS_ONLY", "false").lower() == "true":
        pytest.skip("Skipping expensive tests (FAST_TESTS_ONLY=true)", allow_module_level=True)
```

### Strategy 2: Test Fixtures and Caching 🎯
**Impact: HIGH | Effort: MEDIUM**

#### Session-Scoped Video Fixtures
```python
# In conftest.py
@pytest.fixture(scope="session")
def sample_video_file():
    """Create a single video file for the entire test session."""
    video_file = get_default_video_file()
    yield video_file
    video_file.delete_with_file()

@pytest.fixture(scope="session") 
def processed_video_file(sample_video_file):
    """Create a fully processed video file for the entire test session."""
    # Run pipeline once per session
    video_file = sample_video_file
    video_file.pipe_1(model_name="test_model", delete_frames_after=False)
    mock_video_anonym_annotation_fixture(video_file)
    video_file.pipe_2()
    return video_file
```

#### Usage in Tests
```python
class VideoTestCase(TestCase):
    def test_video_metadata(self, sample_video_file):
        # Use pre-created video instead of creating new one
        assert sample_video_file.video_meta is not None
    
    def test_processed_video(self, processed_video_file):
        # Use pre-processed video for pipeline tests
        assert processed_video_file.is_processed
```

### Strategy 3: Mock and Stub Heavy Operations 🎯
**Impact: HIGH | Effort: MEDIUM**

#### Mock AI Inference
```python
# In conftest.py or test helpers
@pytest.fixture
def mock_ai_inference():
    """Mock AI model inference to avoid expensive computation."""
    with patch('endoreg_db.models.media.video.video_file.VideoFile.pipe_1') as mock:
        mock.return_value = True
        # Set up mock prediction results
        mock.side_effect = lambda **kwargs: create_mock_pipe1_results()
        yield mock

def create_mock_pipe1_results():
    """Create mock results that match pipe_1 expectations."""
    # Create minimal database records without actual processing
    pass
```

#### Mock FFmpeg Operations
```python
@pytest.fixture
def mock_ffmpeg():
    """Mock expensive FFmpeg operations."""
    with patch('endoreg_db.utils.video.ffmpeg_wrapper.extract_frames') as mock_extract:
        mock_extract.return_value = True
        with patch('endoreg_db.utils.video.ffmpeg_wrapper.anonymize_video') as mock_anon:
            mock_anon.return_value = "/path/to/mock/anonymized.mp4"
            yield mock_extract, mock_anon
```

### Strategy 4: Lightweight Test Doubles 🎯
**Impact: MEDIUM | Effort: LOW**

#### Create Test-Specific Video Files
```python
def create_minimal_video_file():
    """Create a minimal VideoFile for testing without expensive operations."""
    video_file = VideoFile.objects.create(
        uuid=uuid.uuid4(),
        center=get_default_center(),
        processor=get_default_processor(),
        # Skip file operations
        raw_file="",
        video_hash="test_hash",
    )
    # Mock essential relationships
    video_file.video_meta = create_mock_video_meta()
    video_file.state = create_mock_video_state()
    return video_file
```

### Strategy 5: Test Categorization and Markers 🎯
**Impact: MEDIUM | Effort: LOW**

#### Pytest Markers
```python
# Add to pytest.ini
markers =
    slow: marks tests as slow (deselect with '-m "not slow"')
    video: marks tests that require video processing
    pipeline: marks tests that run full pipeline
    ai: marks tests that require AI inference
    ffmpeg: marks tests that require FFmpeg
    expensive: marks computationally expensive tests
```

#### Mark Tests Appropriately
```python
@pytest.mark.slow
@pytest.mark.video
@pytest.mark.pipeline
class TestVideoProcessing:
    def test_full_pipeline(self):
        pass

@pytest.mark.expensive
@pytest.mark.ai
def test_ai_inference():
    pass
```

#### Run Selective Tests
```bash
# Run only fast tests
pytest -m "not slow"

# Run without video processing
pytest -m "not video"

# Run without expensive operations
pytest -m "not expensive"

# Run specific categories
pytest -m "video and not ai"
```

### Strategy 6: Database Optimization 🎯
**Impact: MEDIUM | Effort: MEDIUM**

#### Use Transaction Rollbacks
```python
@pytest.mark.django_db(transaction=True)
class VideoTestCase(TransactionTestCase):
    def setUp(self):
        # Create test data
        pass
    
    def tearDown(self):
        # Rollback instead of deleting files
        transaction.rollback()
```

#### Optimize Fixture Loading
```python
# Load fixtures once per test session
@pytest.fixture(scope="session")
def django_db_setup(django_db_setup, django_db_blocker):
    """Load fixtures once for entire test session."""
    with django_db_blocker.unblock():
        load_base_db_data()
        # Cache loaded data
```

## Immediate Action Plan

### Phase 1: Quick Wins (1-2 hours)
1. **Default RUN_VIDEO_TESTS to false**
   ```bash
   # In prod_settings.py
   RUN_VIDEO_TESTS = os.environ.get("RUN_VIDEO_TESTS", "false").lower() == "true"
   ```

2. **Add pytest markers**
   ```ini
   # In pytest.ini
   markers =
       expensive: computationally expensive tests
       video: tests requiring video processing
       slow: slow-running tests
   ```

3. **Add environment-based skipping**
   ```python
   # In test files
   SKIP_EXPENSIVE = os.environ.get("SKIP_EXPENSIVE_TESTS", "true").lower() == "true"
   
   @pytest.mark.skipif(SKIP_EXPENSIVE, reason="Skipping expensive test")
   def test_expensive_operation():
       pass
   ```

### Phase 2: Structural Improvements (4-8 hours)
1. **Implement session-scoped fixtures**
2. **Mock heavy operations in unit tests**
3. **Create lightweight test doubles**
4. **Optimize database fixture loading**

### Phase 3: Advanced Optimizations (1-2 days)
1. **Implement comprehensive mocking strategy**
2. **Create test performance benchmarks**
3. **Parallel test execution setup**
4. **CI/CD integration with test categorization**

## Recommended Test Execution Strategies

### Development Workflow
```bash
# Daily development - fast tests only
pytest -m "not expensive and not video"

# Feature development - include relevant tests
pytest -m "not expensive" tests/requirement/

# Pre-commit - medium test suite
RUN_VIDEO_TESTS=false pytest -m "not slow"

# CI/CD - full test suite (but optimized)
RUN_VIDEO_TESTS=true RUN_PIPELINE_TESTS=true pytest
```

### Performance Targets
- **Fast test suite**: < 30 seconds (unit tests, no video)
- **Medium test suite**: < 5 minutes (integration tests, mocked video)
- **Full test suite**: < 15 minutes (all tests, actual video processing)

## Implementation Priorities

### High Priority 🔥
1. Environment variable controls (immediate)
2. Pytest markers for test categorization (immediate)
3. Session-scoped fixtures (high impact)
4. Mock expensive operations (high impact)

### Medium Priority ⚡
1. Lightweight test doubles
2. Database optimization
3. Test performance monitoring
4. CI/CD integration

### Low Priority 🔧
1. Parallel test execution
2. Test result caching
3. Performance benchmarking dashboard

## Expected Performance Improvements

### Conservative Estimates
- **Fast test mode**: 80-90% reduction in runtime
- **Mock-based tests**: 70-80% reduction in video test runtime
- **Session fixtures**: 50-60% reduction in setup time

### Optimistic Estimates  
- **Combined optimizations**: 90-95% reduction for development workflow
- **CI/CD improvements**: 60-70% reduction for full test suite
- **Developer productivity**: 5-10x improvement in test-driven development cycle

## Configuration Examples

### Environment Variables
```bash
# Fast development mode
export SKIP_EXPENSIVE_TESTS=true
export RUN_VIDEO_TESTS=false
export FAST_TESTS_ONLY=true

# CI/CD mode
export RUN_VIDEO_TESTS=true
export RUN_PIPELINE_TESTS=true
export SKIP_EXPENSIVE_TESTS=false
```

### Pytest Command Examples
```bash
# Development workflow
pytest -m "not expensive" --reuse-db

# Feature testing
pytest -m "not slow" tests/requirement/

# Full regression
pytest --run-expensive tests/

# Specific categories
pytest -m "video and not ai" tests/media/video/
```

This optimization guide provides a systematic approach to reducing test suite overhead while maintaining comprehensive test coverage. The focus is on providing developers with fast feedback loops while ensuring full functionality testing in CI/CD environments.
