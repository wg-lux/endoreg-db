from django.urls import path

from endoreg_db.views import VideoStreamView
from endoreg_db.views.media import (
    PdfMediaView,  # Alias to avoid conflict with legacy pdf.PDFMediaView
    VideoMediaView,
    pdf_sensitive_metadata,
    pdf_sensitive_metadata_list,
    pdf_sensitive_metadata_verify,
    sensitive_metadata_list,
    video_segment_detail,
    video_segment_validate,
    video_segments_by_pk,
    video_segments_by_video,
    video_segments_collection,
    video_segments_stats,
    video_segments_validate_bulk,
    video_segments_validation_status,
    video_sensitive_metadata,
    video_sensitive_metadata_verify,
)
from endoreg_db.views.pdf.pdf_stream import PdfStreamView
from endoreg_db.views.pdf.reimport import PdfReimportView
from endoreg_db.views.video.correction import (
    VideoApplyMaskView,
    VideoCorrectionView,
    VideoMetadataView,
    VideoProcessingHistoryView,
    VideoRemoveFramesView,
)
from endoreg_db.views.video.reimport import VideoReimportView

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
    path(
        "media/videos/<int:pk>/", VideoStreamView.as_view(), name="video-detail-stream"
    ),  # Support ?type= params
    path(
        "media/videos/<int:pk>/details/", VideoMediaView.as_view(), name="video-detail"
    ),  # JSON metadata
    path(
        "media/videos/<int:pk>/stream/", VideoStreamView.as_view(), name="video-stream"
    ),  # Legacy support
    # Video Re-import API endpoint (modern media framework)
    # POST /api/media/videos/<int:pk>/reimport/
    # Re-imports a video file to regenerate metadata when OCR failed or data is incomplete
    path(
        "media/videos/<int:pk>/reimport/",
        VideoReimportView.as_view(),
        name="video-reimport",
    ),
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
    # Video Correction API
    # GET /api/media/videos/video-correction/{id}/ - Get video details for correction
    path(
        "media/videos/video-correction/<int:pk>",
        VideoCorrectionView.as_view(),
        name="video-correction",
    ),
    # Video Metadata API
    # GET /api/media/videos/<int:pk>/metadata/
    # Returns analysis results (sensitive frame count, ratio, frame IDs)
    path(
        "media/videos/<int:pk>/metadata/",
        VideoMetadataView.as_view(),
        name="video-metadata",
    ),
    # Video Processing History API
    # GET /api/media/videos/<int:pk>/processing-history/
    # Returns history of all processing operations (masking, frame removal, analysis)
    path(
        "media/videos/<int:pk>/processing-history/",
        VideoProcessingHistoryView.as_view(),
        name="video-processing-history",
    ),
    # Video Analysis API
    # POST /api/media/videos/<int:pk>/analyze/
    # Analyzes video for sensitive frames using MiniCPM-o 2.6 or OCR+LLM
    # Body: { detection_method: 'minicpm'|'ocr_llm'|'hybrid', sample_interval: 30 }
    # Video Masking API
    # POST /api/media/videos/<int:pk>/apply-mask/
    # Applies device mask or custom ROI mask to video
    # Body: { mask_type: 'device'|'custom', device_name: 'olympus', roi: {...} }
    path(
        "media/videos/<int:pk>/apply-mask/",
        VideoApplyMaskView.as_view(),
        name="video-apply-mask",
    ),
    # Video Frame Removal API
    # POST /api/media/videos/<int:pk>/remove-frames/
    # Removes specified frames from video
    # Body: { frame_list: [10,20,30] OR frame_ranges: '10-20,30' OR detection_method: 'automatic' }
    path(
        "media/videos/<int:pk>/remove-frames/",
        VideoRemoveFramesView.as_view(),
        name="video-remove-frames",
    ),
    # ---------------------------------------------------------------------------------------
    # VIDEO SEGMENT API ENDPOINTS (Modern Media Framework - October 14, 2025)
    #
    # Unified segment management endpoints replacing legacy /api/video-segments/
    # Collection: GET/POST all segments across videos
    # Video-scoped: GET/POST segments for specific video
    # Detail: GET/PATCH/DELETE individual segment
    # ---------------------------------------------------------------------------------------
    # Video Segments Collection API
    # GET/POST /api/media/videos/segments/
    # List all video segments across videos or create new segment
    path(
        "media/videos/segments/",
        video_segments_collection,
        name="video-segments-collection",
    ),
    # Video Segments Stats API
    # GET /api/media/videos/segments/stats/
    # Get statistics about video segments
    path(
        "media/videos/segments/stats/",
        video_segments_stats,
        name="video-segments-stats",
    ),
    # Video-Specific Segments API
    # GET/POST /api/media/videos/<int:pk>/segments/
    # List segments for specific video or create segment for video
    path(
        "media/videos/<int:pk>/segments/",
        video_segments_by_video,
        name="video-segments-by-video",
    ),
    # Segment Detail API
    # GET /api/media/videos/<int:pk>/segments/<int:segment_id>/
    # PATCH /api/media/videos/<int:pk>/segments/<int:segment_id>/
    # DELETE /api/media/videos/<int:pk>/segments/<int:segment_id>/
    # Manages individual segment operations
    path(
        "media/videos/<int:pk>/segments/<int:segment_id>/",
        video_segment_detail,
        name="video-segment-detail",
    ),
    # ---------------------------------------------------------------------------------------
    # VIDEO SEGMENT VALIDATION API ENDPOINTS (Modern Media Framework - October 14, 2025)
    #
    # Unified validation endpoints replacing legacy /api/label-video-segment/*/validate/
    # Single: POST validate individual segment
    # Bulk: POST validate multiple segments
    # Status: GET/POST validation status for all segments
    # ---------------------------------------------------------------------------------------
    # Single Segment Validation API
    # POST /api/media/videos/<int:pk>/segments/<int:segment_id>/validate/
    # Validates a single video segment
    # Body: { "is_validated": true, "notes": "..." }
    path(
        "media/videos/<int:pk>/segments/<int:segment_id>/validate/",
        video_segment_validate,
        name="video-segment-validate",
    ),
    # Bulk Segment Validation API
    # POST /api/media/videos/<int:pk>/segments/validate-bulk/
    # Validates multiple segments at once
    # Body: { "segment_ids": [1,2,3], "is_validated": true, "notes": "..." }
    path(
        "media/videos/<int:pk>/segments/validate-bulk/",
        video_segments_validate_bulk,
        name="video-segments-validate-bulk",
    ),
    # Segment Validation Status API
    # GET /api/media/videos/<int:pk>/segments/validation-status/
    # Returns validation statistics for all segments
    # POST /api/media/videos/<int:pk>/segments/validation-status/
    # Marks all segments (or filtered by label) as validated
    # Body: { "label_name": "polyp", "notes": "..." }
    path(
        "media/videos/<int:pk>/segments/validation-status/",
        video_segments_validation_status,
        name="video-segments-validation-status",
    ),
    # ---------------------------------------------------------------------------------------
    # SENSITIVE METADATA ENDPOINTS (Modern Media Framework)
    # ---------------------------------------------------------------------------------------
    # Video Sensitive Metadata (Resource-Scoped)
    # GET/PATCH /api/media/videos/<pk>/sensitive-metadata/
    # Get or update sensitive patient data for a video
    path(
        "media/videos/<int:pk>/sensitive-metadata/",
        video_sensitive_metadata,
        name="video-sensitive-metadata",
    ),
    # POST /api/media/videos/<pk>/sensitive-metadata/verify/
    # Update verification state (dob_verified, names_verified)
    path(
        "media/videos/<int:pk>/sensitive-metadata/verify/",
        video_sensitive_metadata_verify,
        name="video-sensitive-metadata-verify",
    ),
    # PDF Sensitive Metadata (Resource-Scoped)
    # GET/PATCH /api/media/pdfs/<pk>/sensitive-metadata/
    # Get or update sensitive patient data for a PDF
    path(
        "media/pdfs/<int:pk>/sensitive-metadata/",
        pdf_sensitive_metadata,
        name="pdf-sensitive-metadata",
    ),
    # POST /api/media/pdfs/<pk>/sensitive-metadata/verify/
    # Update verification state (dob_verified, names_verified)
    path(
        "media/pdfs/<int:pk>/sensitive-metadata/verify/",
        pdf_sensitive_metadata_verify,
        name="pdf-sensitive-metadata-verify",
    ),
    # List Endpoints (Collection-Level)
    # GET /api/media/sensitive-metadata/
    # List all sensitive metadata (combined PDFs and Videos)
    # Supports filtering: ?content_type=pdf|video&verified=true&search=name
    path(
        "media/sensitive-metadata/",
        sensitive_metadata_list,
        name="sensitive-metadata-list",
    ),
    # GET /api/media/pdfs/sensitive-metadata/
    # List sensitive metadata for PDFs only
    # Replaces legacy /api/pdf/sensitivemeta/list/
    path(
        "media/pdfs/sensitive-metadata/",
        pdf_sensitive_metadata_list,
        name="pdf-sensitive-metadata-list",
    ),
    # PDF media endpoints
    path("media/pdfs/", PdfMediaView.as_view(), name="pdf-list"),
    path("media/pdfs/<int:pk>/", PdfMediaView.as_view(), name="pdf-detail"),
    path(
        "media/pdfs/<int:pk>/stream/", PdfStreamView.as_view(), name="pdf-stream"
    ),  # Support ?type=raw|anonymized params
    # PDF Re-import API endpoint (modern media framework)
    # POST /api/media/pdfs/<int:pk>/reimport/
    # Re-imports a PDF file to regenerate metadata when OCR failed or data is incomplete
    path(
        "media/pdfs/<int:pk>/reimport/", PdfReimportView.as_view(), name="pdf-reimport"
    ),
]
# ---------------------------------------------------------------------------------------
