from django.urls import path

from endoreg_db.views.media import (
    VideoMediaView,
    PdfMediaView,  # Alias to avoid conflict with legacy pdf.PDFMediaView
    video_segments_by_pk,
)
from endoreg_db.views import (
    VideoStreamView,
)
from endoreg_db.views.pdf.reimport import PdfReimportView
from endoreg_db.views.video.reimport import VideoReimportView
from endoreg_db.views.video.correction import (
    VideoReprocessView,
    VideoMetadataView,
    VideoProcessingHistoryView,
    VideoAnalyzeView,
    VideoApplyMaskView,
    VideoRemoveFramesView,
)
# ---------------------------------------------------------------------------------------
# ANNOTATION API ENDPOINTS
#
# New endpoints for segment annotation management that create user-source segments
# POST /api/annotations/ - Create new annotation (creates user segment if type=segment)
# PATCH /api/annotations/<id>/ - Update annotation (creates user segment if timing/label changed)
# ---------------------------------------------------------------------------------------

# Simplified Meta and Validation Endpoints
    
urlpatterns = [
    # Video media endpoints
    path("media/videos/", VideoMediaView.as_view(), name="video-list"),
    path("media/videos/<int:pk>/", VideoStreamView.as_view(), name="video-detail-stream"),  # Support ?type= params
    path("media/videos/<int:pk>/details/", VideoMediaView.as_view(), name="video-detail"),  # JSON metadata
    path("media/videos/<int:pk>/stream/", VideoStreamView.as_view(), name="video-stream"),  # Legacy support
    
    # Video Re-import API endpoint (modern media framework)
    # POST /api/media/videos/<int:pk>/reimport/
    # Re-imports a video file to regenerate metadata when OCR failed or data is incomplete
    path("media/videos/<int:pk>/reimport/", VideoReimportView.as_view(), name="video-reimport"),
    
    # ---------------------------------------------------------------------------------------
    # VIDEO CORRECTION API ENDPOINTS (Modern Media Framework - October 14, 2025)
    #
    # All video correction endpoints migrated to unified /api/media/videos/<pk>/ pattern
    # These endpoints enable video correction workflows (Phase 1.1):
    # - Analysis: Detect sensitive frames using MiniCPM-o 2.6 or OCR+LLM
    # - Masking: Apply device-specific masks or custom ROI masks
    # - Frame Removal: Remove sensitive frames from videos
    # - Reprocessing: Re-run entire anonymization pipeline
    # - Metadata: View analysis results
    # - History: Track all correction operations
    # ---------------------------------------------------------------------------------------
    
    # Video Metadata API
    # GET /api/media/videos/<int:pk>/metadata/
    # Returns analysis results (sensitive frame count, ratio, frame IDs)
    path("media/videos/<int:pk>/metadata/", VideoMetadataView.as_view(), name="video-metadata"),
    
    # Video Processing History API
    # GET /api/media/videos/<int:pk>/processing-history/
    # Returns history of all processing operations (masking, frame removal, analysis)
    path("media/videos/<int:pk>/processing-history/", VideoProcessingHistoryView.as_view(), name="video-processing-history"),
    
    # Video Analysis API
    # POST /api/media/videos/<int:pk>/analyze/
    # Analyzes video for sensitive frames using MiniCPM-o 2.6 or OCR+LLM
    # Body: { detection_method: 'minicpm'|'ocr_llm'|'hybrid', sample_interval: 30 }
    path("media/videos/<int:pk>/analyze/", VideoAnalyzeView.as_view(), name="video-analyze"),
    
    # Video Masking API
    # POST /api/media/videos/<int:pk>/apply-mask/
    # Applies device mask or custom ROI mask to video
    # Body: { mask_type: 'device'|'custom', device_name: 'olympus', roi: {...} }
    path("media/videos/<int:pk>/apply-mask/", VideoApplyMaskView.as_view(), name="video-apply-mask"),
    
    # Video Frame Removal API
    # POST /api/media/videos/<int:pk>/remove-frames/
    # Removes specified frames from video
    # Body: { frame_list: [10,20,30] OR frame_ranges: '10-20,30' OR detection_method: 'automatic' }
    path("media/videos/<int:pk>/remove-frames/", VideoRemoveFramesView.as_view(), name="video-remove-frames"),
    
    # Video Reprocess API endpoint (modern media framework)
    # POST /api/media/videos/<int:pk>/reprocess/
    # Re-runs entire anonymization pipeline for a video (correction workflow)
    path("media/videos/<int:pk>/reprocess/", VideoReprocessView.as_view(), name="video-reprocess"),
    
    # ---------------------------------------------------------------------------------------
    # VIDEO SEGMENT API ENDPOINTS (Modern Media Framework - October 14, 2025)
    #
    # Unified segment management endpoints
    # ---------------------------------------------------------------------------------------
    
    # Video Segments API (GET with filters, supports label filtering)
    # GET /api/media/videos/<int:pk>/segments/?label=outside
    # Returns all segments for a video, optionally filtered by label name
    path("media/videos/<int:pk>/segments/", video_segments_by_pk, name="video-segments"),

    # PDF media endpoints
    path("media/pdfs/", PdfMediaView.as_view(), name="pdf-list"),
    path("media/pdfs/<int:pk>/", PdfMediaView.as_view(), name="pdf-detail"),
    path("media/pdfs/<int:pk>/stream/", PdfMediaView.as_view(), name="pdf-stream"),

    # PDF Re-import API endpoint (modern media framework)
    # POST /api/media/pdfs/<int:pk>/reimport/
    # Re-imports a PDF file to regenerate metadata when OCR failed or data is incomplete
    path("media/pdfs/<int:pk>/reimport/", PdfReimportView.as_view(), name="pdf-reimport"),
]
    # ---------------------------------------------------------------------------------------
