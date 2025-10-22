# Video Correction Modules Documentation

**Status:** Phase 1.1 Complete (October 2025)  
**Django App:** `endoreg_db`  
**Purpose:** Backend API for video anonymization correction workflows

---

## Overview

The Video Correction module provides REST API endpoints for analyzing, masking, and correcting anonymized medical videos. It integrates with the existing `lx_anonymizer` library to provide human-in-the-loop video correction capabilities.

**Key Components:**
- **Models:** `VideoMetadata`, `VideoProcessingHistory`
- **Serializers:** `VideoMetadataSerializer`, `VideoProcessingHistorySerializer`
- **Views:** 6 APIView classes in `correction.py`
- **Integration:** `FrameCleaner` from `lx_anonymizer`

---

## Architecture

### Data Flow

```
Frontend Request → API View → FrameCleaner → FFmpeg/MiniCPM → Processing History
      ↓                ↓            ↓              ↓                    ↓
  POST /api/    Validate Params  Process Video  Update File     Audit Trail
      ↓                ↓            ↓              ↓                    ↓
  Response      Update Models   Save Output    Return Result   Database Record
```

### File Structure

```
libs/endoreg-db/endoreg_db/
├── models/
│   └── video/
│       └── video_correction.py         # VideoMetadata, VideoProcessingHistory
├── serializers/
│   └── video/
│       ├── video_metadata.py           # VideoMetadataSerializer
│       └── video_processing_history.py # VideoProcessingHistorySerializer
├── views/
│   └── video/
│       └── correction.py               # 6 API view classes
├── urls/
│   └── video.py                        # URL routing
└── migrations/
    └── 0002_add_video_correction_models.py
```

---

## Models

### VideoMetadata

**Purpose:** Stores analysis results for videos analyzed with sensitive frame detection.

**Location:** `endoreg_db/models/video/video_correction.py`

**Fields:**
```python
class VideoMetadata(models.Model):
    video = models.OneToOneField(
        VideoFile, 
        on_delete=models.CASCADE,
        related_name='metadata'
    )
    sensitive_frame_count = models.IntegerField(
        null=True, 
        blank=True,
        help_text="Number of frames containing sensitive information"
    )
    sensitive_ratio = models.FloatField(
        null=True, 
        blank=True,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text="Ratio of sensitive frames to total frames (0.0-1.0)"
    )
    sensitive_frame_ids = models.TextField(
        null=True, 
        blank=True,
        help_text="JSON array of sensitive frame numbers, e.g., '[10, 15, 20]'"
    )
    analyzed_at = models.DateTimeField(auto_now=True)
```

**Properties:**
```python
@property
def has_analysis(self) -> bool:
    """Returns True if analysis has been run"""
    return self.sensitive_frame_count is not None

@property
def sensitive_percentage(self) -> float:
    """Returns sensitive ratio as percentage (0-100)"""
    if self.sensitive_ratio is None:
        return 0.0
    return self.sensitive_ratio * 100
```

**Usage Example:**
```python
from endoreg_db.models import VideoFile, VideoMetadata

video = VideoFile.objects.get(pk=1)
metadata, created = VideoMetadata.objects.get_or_create(video=video)

# After analysis
metadata.sensitive_frame_count = 15
metadata.sensitive_ratio = 0.05  # 5% of frames
metadata.sensitive_frame_ids = '[10, 15, 20, 25, 30]'
metadata.save()

print(f"Sensitive: {metadata.sensitive_percentage:.1f}%")  # "5.0%"
print(f"Has analysis: {metadata.has_analysis}")  # True
```

---

### VideoProcessingHistory

**Purpose:** Audit trail for all video correction operations (masking, frame removal, analysis).

**Location:** `endoreg_db/models/video/video_correction.py`

**Fields:**
```python
class VideoProcessingHistory(models.Model):
    # Operation Types
    OPERATION_ANALYSIS = 'analysis'
    OPERATION_MASKING = 'mask_overlay'
    OPERATION_FRAME_REMOVAL = 'frame_removal'
    OPERATION_REPROCESSING = 'reprocessing'
    
    OPERATION_CHOICES = [
        (OPERATION_ANALYSIS, 'Video Analysis'),
        (OPERATION_MASKING, 'Mask Overlay'),
        (OPERATION_FRAME_REMOVAL, 'Frame Removal'),
        (OPERATION_REPROCESSING, 'Full Reprocessing'),
    ]
    
    # Status Types
    STATUS_PENDING = 'pending'
    STATUS_RUNNING = 'running'
    STATUS_SUCCESS = 'success'
    STATUS_FAILURE = 'failure'
    
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_RUNNING, 'Running'),
        (STATUS_SUCCESS, 'Success'),
        (STATUS_FAILURE, 'Failure'),
    ]
    
    video = models.ForeignKey(
        VideoFile, 
        on_delete=models.CASCADE,
        related_name='processing_history'
    )
    operation = models.CharField(max_length=50, choices=OPERATION_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    config = models.JSONField(
        default=dict,
        help_text="Operation configuration (mask type, frame list, etc.)"
    )
    output_file = models.CharField(max_length=500, null=True, blank=True)
    details = models.TextField(blank=True)
    task_id = models.CharField(max_length=100, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
```

**Properties:**
```python
@property
def duration(self) -> Optional[float]:
    """Returns processing duration in seconds"""
    if self.completed_at and self.created_at:
        return (self.completed_at - self.created_at).total_seconds()
    return None

@property
def is_complete(self) -> bool:
    """Returns True if operation is finished (success or failure)"""
    return self.status in [self.STATUS_SUCCESS, self.STATUS_FAILURE]
```

**Methods:**
```python
def mark_success(self, output_file: str = None, details: str = None):
    """Mark operation as successful"""
    self.status = self.STATUS_SUCCESS
    self.completed_at = timezone.now()
    if output_file:
        self.output_file = output_file
    if details:
        self.details = details
    self.save()

def mark_failure(self, error_message: str):
    """Mark operation as failed"""
    self.status = self.STATUS_FAILURE
    self.completed_at = timezone.now()
    self.details = error_message
    self.save()
```

**Usage Example:**
```python
from endoreg_db.models import VideoProcessingHistory

# Create record at operation start
history = VideoProcessingHistory.objects.create(
    video=video,
    operation=VideoProcessingHistory.OPERATION_MASKING,
    config={'mask_type': 'device', 'device_name': 'olympus'}
)

try:
    # Perform masking operation...
    output_path = "/media/anonym_videos/abc123_masked.mp4"
    
    # Mark success
    history.mark_success(
        output_file=output_path,
        details="Applied Olympus CV-1500 device mask"
    )
except Exception as e:
    # Mark failure
    history.mark_failure(str(e))

print(f"Duration: {history.duration:.2f}s")  # "45.23s"
```

---

## Serializers

### VideoMetadataSerializer

**Purpose:** Serializes VideoMetadata with JSON parsing and validation.

**Location:** `endoreg_db/serializers/video/video_metadata.py`

**Fields:**
```python
class VideoMetadataSerializer(serializers.ModelSerializer):
    sensitive_frame_ids_list = serializers.SerializerMethodField()
    sensitive_percentage = serializers.ReadOnlyField()
    has_analysis = serializers.ReadOnlyField()
    
    class Meta:
        model = VideoMetadata
        fields = [
            'id', 'video', 'sensitive_frame_count', 'sensitive_ratio',
            'sensitive_frame_ids', 'sensitive_frame_ids_list',
            'sensitive_percentage', 'has_analysis', 'analyzed_at'
        ]
        read_only_fields = ['id', 'analyzed_at']
```

**Key Methods:**
```python
def get_sensitive_frame_ids_list(self, obj) -> List[int]:
    """Parse JSON string '[10,15,20]' to Python list [10,15,20]"""
    if not obj.sensitive_frame_ids:
        return []
    try:
        return json.loads(obj.sensitive_frame_ids)
    except (json.JSONDecodeError, TypeError):
        return []

def validate_sensitive_frame_ids(self, value: str) -> str:
    """Validate JSON array format with integer elements"""
    if not value:
        return value
    
    try:
        parsed = json.loads(value)
        if not isinstance(parsed, list):
            raise serializers.ValidationError("Must be a JSON array")
        if not all(isinstance(x, int) for x in parsed):
            raise serializers.ValidationError("Array must contain only integers")
        return value
    except json.JSONDecodeError:
        raise serializers.ValidationError("Invalid JSON format")

def validate_sensitive_ratio(self, value: float) -> float:
    """Validate ratio is between 0.0 and 1.0"""
    if value is not None and not (0.0 <= value <= 1.0):
        raise serializers.ValidationError("Ratio must be between 0.0 and 1.0")
    return value
```

**API Response Example:**
```json
{
  "id": 1,
  "video": 42,
  "sensitive_frame_count": 15,
  "sensitive_ratio": 0.05,
  "sensitive_frame_ids": "[10, 15, 20, 25, 30]",
  "sensitive_frame_ids_list": [10, 15, 20, 25, 30],
  "sensitive_percentage": 5.0,
  "has_analysis": true,
  "analyzed_at": "2025-10-09T14:30:00Z"
}
```

---

### VideoProcessingHistorySerializer

**Purpose:** Serializes processing history with download URLs and operation-specific validation.

**Location:** `endoreg_db/serializers/video/video_processing_history.py`

**Fields:**
```python
class VideoProcessingHistorySerializer(serializers.ModelSerializer):
    operation_display = serializers.CharField(source='get_operation_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    download_url = serializers.SerializerMethodField()
    duration = serializers.ReadOnlyField()
    is_complete = serializers.ReadOnlyField()
    
    class Meta:
        model = VideoProcessingHistory
        fields = [
            'id', 'video', 'operation', 'operation_display', 'status', 'status_display',
            'config', 'output_file', 'download_url', 'details', 'task_id',
            'created_at', 'completed_at', 'duration', 'is_complete'
        ]
        read_only_fields = ['id', 'created_at', 'completed_at']
```

**Key Methods:**
```python
def get_download_url(self, obj) -> Optional[str]:
    """Generate download URL for processed video"""
    if obj.output_file and obj.status == VideoProcessingHistory.STATUS_SUCCESS:
        return f"/api/media/processed-videos/{obj.video.id}/{obj.id}/"
    return None

def validate_config(self, value: dict) -> dict:
    """Validate operation-specific configuration"""
    operation = self.initial_data.get('operation')
    
    if operation == VideoProcessingHistory.OPERATION_MASKING:
        # Masking requires mask_type
        if 'mask_type' not in value:
            raise serializers.ValidationError("Masking requires 'mask_type' in config")
        
        # Device masking requires device_name
        if value['mask_type'] == 'device' and 'device_name' not in value:
            raise serializers.ValidationError("Device masking requires 'device_name'")
        
        # Custom masking requires roi
        if value['mask_type'] == 'custom' and 'roi' not in value:
            raise serializers.ValidationError("Custom masking requires 'roi'")
    
    elif operation == VideoProcessingHistory.OPERATION_FRAME_REMOVAL:
        # Frame removal requires frame_list OR detection_method
        if 'frame_list' not in value and 'detection_method' not in value:
            raise serializers.ValidationError(
                "Frame removal requires 'frame_list' or 'detection_method'"
            )
    
    return value
```

**API Response Example:**
```json
{
  "id": 5,
  "video": 42,
  "operation": "mask_overlay",
  "operation_display": "Mask Overlay",
  "status": "success",
  "status_display": "Success",
  "config": {
    "mask_type": "device",
    "device_name": "olympus"
  },
  "output_file": "anonym_videos/abc123_masked.mp4",
  "download_url": "/api/media/processed-videos/42/5/",
  "details": "Applied Olympus CV-1500 device mask",
  "task_id": null,
  "created_at": "2025-10-09T14:25:00Z",
  "completed_at": "2025-10-09T14:26:15Z",
  "duration": 75.3,
  "is_complete": true
}
```

---

## API Views

### VideoMetadataView

**Endpoint:** `GET /api/video-metadata/<id>/`

**Purpose:** Returns analysis results for a video, or creates empty metadata record if none exists.

**Implementation:**
```python
class VideoMetadataView(APIView):
    def get(self, request, id):
        video = get_object_or_404(VideoFile, pk=id)
        metadata, created = VideoMetadata.objects.get_or_create(video=video)
        serializer = VideoMetadataSerializer(metadata)
        return Response(serializer.data)
```

**Response Example:**
```json
{
  "id": 1,
  "video": 42,
  "sensitive_frame_count": null,
  "sensitive_ratio": null,
  "sensitive_frame_ids": null,
  "sensitive_frame_ids_list": [],
  "sensitive_percentage": 0.0,
  "has_analysis": false,
  "analyzed_at": "2025-10-09T14:30:00Z"
}
```

**Use Case:** Frontend loads this on correction component mount to check if analysis has been run.

---

### VideoProcessingHistoryView

**Endpoint:** `GET /api/video-processing-history/<id>/`

**Purpose:** Returns all processing operations for a video, ordered by newest first.

**Implementation:**
```python
class VideoProcessingHistoryView(APIView):
    def get(self, request, id):
        video = get_object_or_404(VideoFile, pk=id)
        history = VideoProcessingHistory.objects.filter(video=video).order_by('-created_at')
        serializer = VideoProcessingHistorySerializer(history, many=True)
        return Response(serializer.data)
```

**Response Example:**
```json
[
  {
    "id": 5,
    "operation": "mask_overlay",
    "status": "success",
    "created_at": "2025-10-09T14:25:00Z",
    "duration": 75.3
  },
  {
    "id": 3,
    "operation": "analysis",
    "status": "success",
    "created_at": "2025-10-09T14:20:00Z",
    "duration": 120.5
  }
]
```

**Use Case:** Display operation audit trail in frontend correction component.

---

### VideoAnalyzeView (TODO: CURRENTLY NEEDS REWORK SINCE METHOD DEPRECATED IN LX ANONYMIZER)

**Endpoint:** `POST /api/video-analyze/<id>/`

**Purpose:** Analyzes video for sensitive frames using MiniCPM-o 2.6 or OCR+LLM.

**Request Parameters:**
```json
{
  "detection_method": "minicpm",  // "minicpm" | "ocr_llm" | "hybrid"
  "sample_interval": 30           // Optional: frames to skip between analysis
}
```

**Implementation:**
```python
class VideoAnalyzeView(APIView):
    def post(self, request, id):
        video = get_object_or_404(VideoFile, pk=id)
        detection_method = request.data.get('detection_method', 'minicpm')
        sample_interval = request.data.get('sample_interval')
        
        # Initialize FrameCleaner
        use_minicpm = detection_method in ['minicpm', 'hybrid']
        frame_cleaner = FrameCleaner(use_minicpm=use_minicpm)
        
        # Run analysis
        analysis_result = frame_cleaner.analyze_video_sensitivity(
            video_path=video.raw_file.path,
            sample_interval=sample_interval
        )
        
        # Update metadata
        metadata, _ = VideoMetadata.objects.update_or_create(
            video=video,
            defaults={
                'sensitive_frame_count': len(analysis_result['sensitive_frame_ids']),
                'sensitive_ratio': len(analysis_result['sensitive_frame_ids']) / analysis_result['total_frames'],
                'sensitive_frame_ids': json.dumps(analysis_result['sensitive_frame_ids'])
            }
        )
        
        # Create history record
        VideoProcessingHistory.objects.create(
            video=video,
            operation=VideoProcessingHistory.OPERATION_ANALYSIS,
            status=VideoProcessingHistory.STATUS_SUCCESS,
            config={'detection_method': detection_method, 'sample_interval': sample_interval}
        )
        
        return Response({
            'sensitive_frame_count': metadata.sensitive_frame_count,
            'total_frames': analysis_result['total_frames'],
            'sensitive_ratio': metadata.sensitive_ratio,
            'sensitive_frame_ids': analysis_result['sensitive_frame_ids'],
            'sensitive_percentage': metadata.sensitive_percentage,
            'detection_method': detection_method,
            'message': f'Analysis complete: {metadata.sensitive_frame_count} sensitive frames found'
        })
```

**Response Example:**
```json
{
  "sensitive_frame_count": 15,
  "total_frames": 300,
  "sensitive_ratio": 0.05,
  "sensitive_frame_ids": [10, 15, 20, 25, 30],
  "sensitive_percentage": 5.0,
  "detection_method": "minicpm",
  "message": "Analysis complete: 15 sensitive frames found"
}
```

**Use Case:** User clicks "Video analysieren" button in frontend to detect sensitive frames.

---

### VideoApplyMaskView

**Endpoint:** `POST /api/video-apply-mask/<id>/`

**Purpose:** Applies device-specific or custom ROI mask to video.

**Request Parameters:**
```json
{
  "mask_type": "device",          // "device" | "custom"
  "device_name": "olympus",       // Required if mask_type=device
  "roi": {                        // Required if mask_type=custom
    "x": 100,
    "y": 50,
    "width": 800,
    "height": 600
  },
  "processing_method": "streaming" // "streaming" | "direct"
}
```

**Implementation:**
```python
class VideoApplyMaskView(APIView):
    def post(self, request, id):
        video = get_object_or_404(VideoFile, pk=id)
        mask_type = request.data.get('mask_type')
        
        # Create processing history
        history = VideoProcessingHistory.objects.create(
            video=video,
            operation=VideoProcessingHistory.OPERATION_MASKING,
            status=VideoProcessingHistory.STATUS_PENDING,
            config=request.data
        )
        
        try:
            frame_cleaner = FrameCleaner()
            
            # Load mask config
            if mask_type == 'device':
                device_name = request.data.get('device_name')
                mask_config = frame_cleaner._load_mask(device_name)
            else:  # custom
                roi = request.data.get('roi')
                mask_config = frame_cleaner._create_mask_config_from_roi(roi)
            
            # Apply mask
            output_path = Path(settings.MEDIA_ROOT) / 'anonym_videos' / f"{video.uuid}_masked.mp4"
            success = frame_cleaner._mask_video(
                input_video=video.raw_file.path,
                mask_config=mask_config,
                output_video=output_path
            )
            
            if success:
                # Update video
                video.anonymized_file = f"anonym_videos/{video.uuid}_masked.mp4"
                video.save()
                
                # Mark success
                history.mark_success(
                    output_file=str(output_path),
                    details=f"Applied {mask_type} mask"
                )
                
                return Response({
                    'task_id': None,  # TODO: Celery task ID in Phase 1.2
                    'output_file': str(output_path),
                    'message': 'Masking complete',
                    'processing_time': history.duration
                })
            else:
                raise Exception("Masking operation failed")
                
        except Exception as e:
            history.mark_failure(str(e))
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
```

**Response Example:**
```json
{
  "task_id": null,
  "output_file": "/media/anonym_videos/abc123_masked.mp4",
  "message": "Masking complete",
  "processing_time": 45.2
}
```

**Available Device Masks:**
- `olympus` - Olympus CV-1500
- `pentax` - Pentax EPT-7000
- `fujifilm` - Fujifilm 4450HD

**Use Case:** User selects device mask or draws custom ROI, clicks "Maske anwenden".

---

### VideoRemoveFramesView

**Endpoint:** `POST /api/video-remove-frames/<id>/`

**Purpose:** Removes specified frames from video.

**Request Parameters:**
```json
{
  "frame_list": [10, 15, 20, 30],     // Manual frame numbers
  "frame_ranges": "10-20,30,45-50",   // Range string (alternative to frame_list)
  "detection_method": "automatic",    // Use VideoMetadata.sensitive_frame_ids
  "processing_method": "streaming"    // "streaming" | "traditional"
}
```

**Implementation:**
```python
class VideoRemoveFramesView(APIView):
    def post(self, request, id):
        video = get_object_or_404(VideoFile, pk=id)
        
        # Determine frames to remove
        detection_method = request.data.get('detection_method')
        if detection_method == 'automatic':
            metadata = VideoMetadata.objects.get(video=video)
            frames_to_remove = json.loads(metadata.sensitive_frame_ids)
        elif 'frame_ranges' in request.data:
            frames_to_remove = self._parse_frame_ranges(request.data['frame_ranges'])
        else:
            frames_to_remove = request.data.get('frame_list', [])
        
        # Create processing history
        history = VideoProcessingHistory.objects.create(
            video=video,
            operation=VideoProcessingHistory.OPERATION_FRAME_REMOVAL,
            config={
                'frame_list': frames_to_remove,
                'detection_method': detection_method
            }
        )
        
        try:
            frame_cleaner = FrameCleaner()
            output_path = Path(settings.MEDIA_ROOT) / 'anonym_videos' / f"{video.uuid}_cleaned.mp4"
            
            success = frame_cleaner.remove_frames_from_video(
                original_video=video.raw_file.path,
                frames_to_remove=frames_to_remove,
                output_video=output_path,
                total_frames=video.total_frames
            )
            
            if success:
                video.anonymized_file = f"anonym_videos/{video.uuid}_cleaned.mp4"
                video.save()
                
                # TODO Phase 1.4: Update segments
                # update_segments_after_frame_removal(video, frames_to_remove)
                
                history.mark_success(str(output_path))
                
                return Response({
                    'task_id': None,
                    'output_file': str(output_path),
                    'frames_removed': len(frames_to_remove),
                    'message': f'Removed {len(frames_to_remove)} frames',
                    'processing_time': history.duration
                })
                
        except Exception as e:
            history.mark_failure(str(e))
            return Response({'error': str(e)}, status=500)
    
    def _parse_frame_ranges(self, ranges_str: str) -> List[int]:
        """Parse '10-20,30,45-50' to [10,11,...,20,30,45,...,50]"""
        frames = []
        for part in ranges_str.split(','):
            part = part.strip()
            if '-' in part:
                start, end = map(int, part.split('-'))
                frames.extend(range(start, end + 1))
            else:
                frames.append(int(part))
        return sorted(set(frames))
```

**Response Example:**
```json
{
  "task_id": null,
  "output_file": "/media/anonym_videos/abc123_cleaned.mp4",
  "frames_removed": 15,
  "message": "Removed 15 frames",
  "processing_time": 62.8
}
```

**Use Case:** User manually selects frames or uses automatic detection to remove sensitive frames.

---

### VideoReimportView

"""
VideoReimportView provides an API endpoint for re-importing a video file and regenerating its metadata, particularly SensitiveMeta. This is intended for cases where OCR or metadata extraction failed or was incomplete during the initial import.

Features:
- Validates the provided video ID (pk) and checks for the existence of the video and its raw file.
- Ensures the video has an associated center and required relationships.
- Clears any existing SensitiveMeta and deletes the old record to force regeneration.
- Re-initializes video specifications and frames.
- Runs OCR and AI processing (Pipe 1) to extract new metadata.
- Ensures minimum patient data is present.
- Uses VideoImportService to anonymize and process the video file.
- Handles errors gracefully, including storage issues and processing failures.
- Returns detailed status and metadata about the re-import operation.

Methods:
- post(request, pk): Handles the re-import process for the video with the given primary key.
    - Args:
        - request: The HTTP request object.
        - pk (int): Primary key of the VideoFile to re-import.
    - Returns:
        - Response: JSON response indicating success or failure, with relevant metadata.
"""

## URL Configuration

**Location:** `libs/endoreg-db/endoreg_db/urls/video.py`

```python
from endoreg_db.views.video import (
    VideoMetadataView,
    VideoProcessingHistoryView,
    VideoApplyMaskView,
    VideoRemoveFramesView,
)

urlpatterns = [
    # ...existing patterns...
    
    # Video Correction (Phase 1.1)
    path('video-metadata/<int:id>/', VideoMetadataView.as_view(), name='video_metadata'),
    path('video-processing-history/<int:id>/', VideoProcessingHistoryView.as_view(), name='video_processing_history'),
    path('video-analyze/<int:id>/', VideoAnalyzeView.as_view(), name='video_analyze'),
    path('video-apply-mask/<int:id>/', VideoApplyMaskView.as_view(), name='video_apply_mask'),
    path('video-remove-frames/<int:id>/', VideoRemoveFramesView.as_view(), name='video_remove_frames'),
]
```

**Full Endpoint URLs** (with `/api/` prefix from `lx_annotate/urls.py`):
- `GET /api/video-metadata/<id>/`
- `GET /api/video-processing-history/<id>/`
- `POST /api/video-analyze/<id>/`
- `POST /api/video-apply-mask/<id>/`
- `POST /api/video-remove-frames/<id>/`

---

## Integration with lx_anonymizer

### FrameCleaner Methods

**Location:** `libs/lx-anonymizer/lx_anonymizer/frame_cleaner.py`

#### analyze_video_sensitivity()

**Purpose:** Detects sensitive frames using MiniCPM-o 2.6 or OCR+LLM.

**Signature:**
```python
def analyze_video_sensitivity(
    self,
    video_path: str,
    sample_interval: Optional[int] = None
) -> dict
```

**Returns:**
```python
{
    'total_frames': 300,
    'sensitive_frame_ids': [10, 15, 20, 25, 30],
    'detection_method': 'minicpm'
}
```

**Usage in VideoAnalyzeView:**
```python
frame_cleaner = FrameCleaner(use_minicpm=True)
result = frame_cleaner.analyze_video_sensitivity(
    video_path=video.raw_file.path,
    sample_interval=30
)
```

---

#### _mask_video()

**Purpose:** Applies FFmpeg masking with NVENC hardware acceleration.

**Signature:**
```python
def _mask_video(
    self,
    input_video: str,
    mask_config: dict,
    output_video: str
) -> bool
```

**Mask Config Format:**
```python
{
    "image_width": 1920,
    "image_height": 1080,
    "endoscope_image_x": 550,
    "endoscope_image_y": 0,
    "endoscope_image_width": 1350,
    "endoscope_image_height": 1080
}
```

**Usage in VideoApplyMaskView:**
```python
frame_cleaner = FrameCleaner()
mask_config = frame_cleaner._load_mask('olympus')
success = frame_cleaner._mask_video(
    input_video=video.raw_file.path,
    mask_config=mask_config,
    output_video='/media/anonym_videos/output.mp4'
)
```

---

#### remove_frames_from_video()

**Purpose:** Removes specified frames using FFmpeg.

**Signature:**
```python
def remove_frames_from_video(
    self,
    original_video: str,
    frames_to_remove: List[int],
    output_video: str,
    total_frames: int
) -> bool
```

**Usage in VideoRemoveFramesView:**
```python
frame_cleaner = FrameCleaner()
success = frame_cleaner.remove_frames_from_video(
    original_video=video.raw_file.path,
    frames_to_remove=[10, 15, 20],
    output_video='/media/anonym_videos/cleaned.mp4',
    total_frames=300
)
```

---

### Device Masks

**Location:** `libs/lx-anonymizer/lx_anonymizer/masks/`

**Available Masks:**
- `olympus_cv_1500_mask.json` - Olympus CV-1500
- `pentax_ept_7000_mask.json` - Pentax EPT-7000
- `fujifilm_4450hd_mask.json` - Fujifilm 4450HD

**Loading Device Mask:**
```python
frame_cleaner = FrameCleaner()
mask_config = frame_cleaner._load_mask('olympus')
# Returns dict with coordinates
```

**Creating Custom Mask from ROI:**
```python
roi = {'x': 100, 'y': 50, 'width': 800, 'height': 600}
mask_config = frame_cleaner._create_mask_config_from_roi(roi)
```

---

## Testing

### Django Shell Tests

**Verify Imports:**
```bash
python manage.py shell -c "
from endoreg_db.views import VideoMetadataView, VideoAnalyzeView
from endoreg_db.models import VideoMetadata, VideoProcessingHistory
from endoreg_db.serializers import VideoMetadataSerializer

print('✅ All imports successful')
print('VideoMetadataView:', VideoMetadataView)
"
```

**Expected Output:**
```
144 objects imported automatically.
✅ All imports successful
VideoMetadataView: <class 'endoreg_db.views.video.correction.VideoMetadataView'>
```

### Manual API Testing

**Test Video Analysis:**
```bash
curl -X POST http://localhost:8000/api/video-analyze/1/ \
  -H "Content-Type: application/json" \
  -d '{"detection_method": "minicpm", "sample_interval": 30}'
```

**Test Device Masking:**
```bash
curl -X POST http://localhost:8000/api/video-apply-mask/1/ \
  -H "Content-Type: application/json" \
  -d '{
    "mask_type": "device",
    "device_name": "olympus",
    "processing_method": "streaming"
  }'
```

**Test Frame Removal:**
```bash
curl -X POST http://localhost:8000/api/video-remove-frames/1/ \
  -H "Content-Type: application/json" \
  -d '{
    "frame_ranges": "10-20,30,45-50",
    "processing_method": "streaming"
  }'
```

---

## Migration

**Location:** `libs/endoreg-db/endoreg_db/migrations/0002_add_video_correction_models.py`

**Created Tables:**
- `video_metadata` - Analysis results storage
- `video_processing_history` - Operation audit trail

**Apply Migration:**
```bash
python manage.py migrate endoreg_db
```

**Verify:**
```bash
python manage.py showmigrations endoreg_db
```

**Expected Output:**
```
endoreg_db
 [X] 0001_initial
 [X] 0002_add_video_correction_models
```

---

## Future Enhancements (Phase 1.2+)

### Celery Task Integration (Phase 1.2)

**Planned Changes:**
- Convert all synchronous operations to Celery tasks
- Add `TaskStatusView` for progress tracking
- Return `task_id` in responses instead of blocking

**Example Task:**
```python
from celery import shared_task

@shared_task(bind=True)
def apply_mask_task(self, video_id, mask_config):
    self.update_state(state='PROGRESS', meta={'current': 0, 'total': 100})
    
    video = VideoFile.objects.get(pk=video_id)
    frame_cleaner = FrameCleaner()
    
    # ... masking logic with progress updates ...
    
    return {'success': True, 'output_path': str(output_path)}
```

### Segment Update Logic (Phase 1.4)

**Planned Function:**
```python
def update_segments_after_frame_removal(video, removed_frames):
    """Shift frame numbers in LabelVideoSegments after removal"""
    segments = LabelVideoSegment.objects.filter(video=video).order_by('start_frame')
    
    for segment in segments:
        # Count frames removed before this segment
        frames_before = sum(1 for f in removed_frames if f < segment.start_frame)
        
        # Shift segment boundaries
        segment.start_frame -= frames_before
        segment.end_frame -= frames_before
        segment.save()
```

---

## Troubleshooting

### Common Issues

**1. "Module 'endoreg_db.views.video.correction' has no attribute 'VideoAnalyzeView'"**

**Solution:** Ensure imports are updated in `__init__.py` files:
```python
# views/video/__init__.py
from .correction import (
    VideoMetadataView,
    # ...
)
```

**2. "No module named 'lx_anonymizer'"**

**Solution:** Verify `lx-anonymizer` is in `INSTALLED_APPS` and `sys.path`:
```python
# settings.py
INSTALLED_APPS = [
    # ...
    'lx_anonymizer',
]
```

**3. "Table 'video_metadata' doesn't exist"**

**Solution:** Apply migrations:
```bash
python manage.py migrate endoreg_db
```

**4. "CUDA out of memory" during MiniCPM analysis**

**Solution:** Reduce `sample_interval` or fall back to OCR:
```json
{
  "detection_method": "ocr_llm",
  "sample_interval": 60
}
```

---

## References

- **Frontend Component:** `/frontend/src/components/Anonymizer/AnonymizationCorrectionComponent.vue`
- **lx_anonymizer:** `/libs/lx-anonymizer/lx_anonymizer/frame_cleaner.py`
- **Device Masks:** `/libs/lx-anonymizer/lx_anonymizer/masks/`
- **Main Documentation:** `/docs/ANONYMIZER.md`
- **Migration:** `/libs/endoreg-db/endoreg_db/migrations/0002_add_video_correction_models.py`
