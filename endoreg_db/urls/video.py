from django.urls import path

from endoreg_db.views import (
    VideoReimportView,
    SensitiveMetaDetailView,
    VideoLabelView,
    VideoStreamView,
    # Video Correction Views (Phase 1.1)
    VideoMetadataView,
    VideoProcessingHistoryView,
    VideoAnalyzeView,
    VideoApplyMaskView,
    VideoRemoveFramesView,
    VideoReprocessView,
)

url_patterns = [
    # Video Label segments API endpoint
    # GET /api/videos/<int:video_id>/labels/<str:label_name>/
    # Retrieves time segments and frame predictions for a specific label on a video
    path(
        'videos/<int:video_id>/labels/<str:label_name>/', 
        VideoLabelView.as_view(), 
        name='video_label_segments'
    ),
    
    # Video streaming API endpoint
    # GET /api/videostream/<int:pk>/
    # Streams the raw video file with HTTP range and CORS support
    path(
        'videostream/<int:pk>/', 
        VideoStreamView.as_view(), 
        name='video_stream'
    ),
    
    # Video Re-import API endpoint
    # POST /api/video/<int:video_id>/reimport/
    # Re-imports a video file to regenerate metadata when OCR failed or data is incomplete
    path(
        'video/<int:video_id>/reimport/', 
        VideoReimportView.as_view(), 
        name='video_reimport'
    ),
    # Video Sensitive Meta endpoints (for video anonymization)
    # GET /api/video/sensitivemeta/<int:sensitive_meta_id>/
    # PATCH /api/video/sensitivemeta/<int:sensitive_meta_id>/
    path(
        'video/sensitivemeta/<int:sensitive_meta_id>/', 
        SensitiveMetaDetailView.as_view(), 
        name='video_sensitive_meta_detail'
    ),
    
    # ---------------------------------------------------------------------------------------
    # VIDEO CORRECTION API ENDPOINTS (Phase 1.1)
    #
    # These endpoints enable video correction workflows:
    # - Analysis: Detect sensitive frames using MiniCPM-o 2.6 or OCR+LLM
    # - Masking: Apply device-specific masks or custom ROI masks
    # - Frame Removal: Remove sensitive frames from videos
    # - Processing History: Track all correction operations
    # ---------------------------------------------------------------------------------------
    
    # Video Metadata API
    # GET /api/video-metadata/<int:id>/
    # Returns analysis results (sensitive frame count, ratio, frame IDs)
    path(
        'video-metadata/<int:id>/',
        VideoMetadataView.as_view(),
        name='video_metadata'
    ),
    
    # Video Processing History API
    # GET /api/video-processing-history/<int:id>/
    # Returns history of all processing operations (masking, frame removal, analysis)
    path(
        'video-processing-history/<int:id>/',
        VideoProcessingHistoryView.as_view(),
        name='video_processing_history'
    ),
    
    # Video Analysis API
    # POST /api/video-analyze/<int:id>/
    # Analyzes video for sensitive frames using MiniCPM-o 2.6 or OCR+LLM
    # Body: { detection_method: 'minicpm'|'ocr_llm'|'hybrid', sample_interval: 30 }
    path(
        'video-analyze/<int:id>/',
        VideoAnalyzeView.as_view(),
        name='video_analyze'
    ),
    
    # Video Masking API
    # POST /api/video-apply-mask/<int:id>/
    # Applies device mask or custom ROI mask to video
    # Body: { mask_type: 'device'|'custom', device_name: 'olympus', roi: {...} }
    path(
        'video-apply-mask/<int:id>/',
        VideoApplyMaskView.as_view(),
        name='video_apply_mask'
    ),
    
    # Frame Removal API
    # POST /api/video-remove-frames/<int:id>/
    # Removes specified frames from video
    # Body: { frame_list: [10,20,30] OR frame_ranges: '10-20,30' OR detection_method: 'automatic' }
    path(
        'video-remove-frames/<int:id>/',
        VideoRemoveFramesView.as_view(),
        name='video_remove_frames'
    ),
    
    # Video Reprocessing API
    # POST /api/video-reprocess/<int:id>/
    # Re-runs entire anonymization pipeline for a video
    path(
        'video-reprocess/<int:id>/',
        VideoReprocessView.as_view(),
        name='video_reprocess'
    ),
]