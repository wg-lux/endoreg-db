from django.urls import path

from endoreg_db.views.media.frame_media import FrameStreamView
from endoreg_db.views.media.hub import (
    HubTransferCreateView,
    HubTransferMediaUploadView,
    HubTransferStatusView,
)
from endoreg_db.views.media.patient_media_timeline import PatientMediaTimelineView
from endoreg_db.views.media.pdf_media import (
    PdfMediaView,  # Alias to avoid conflict with legacy pdf.reportMediaView
)
from endoreg_db.views.media.sensitive_metadata import (
    get_sensitive_metadata_pk,
    pdf_case_resolution,
    pdf_sensitive_metadata,
    pdf_sensitive_metadata_list,
    pdf_sensitive_metadata_verify,
    sensitive_metadata_list,
    video_case_resolution,
    video_sensitive_metadata,
    video_sensitive_metadata_verify,
)
from endoreg_db.views.media.video_media import VideoMediaView
from endoreg_db.views.report.reimport import ReportReimportView
from endoreg_db.views.report.pdf_redaction import (
    PdfApplyRedactionsView,
    PdfProcessingHistoryView,
)
from endoreg_db.views.report.report_stream import ReportStreamView
from endoreg_db.views.video.video_stream import VideoStreamView
from endoreg_db.views.video import (
    VideoReimportView,
    VideoFpsView,
    export_annotated_data,
    video_segment_detail,
    video_segment_validate,
    video_segments_blacken_outside,
    video_segments_bulk_mutation,
    video_segments_by_video,
    video_segments_stats,
    video_segments_validate_bulk,
    video_segments_validation_status,
    import_prediction_segments_to_manual,
    ensure_segment_annotations_bulk,
    ensure_segment_annotations_for_video,
    ensure_prediction_segment_annotations_bulk,
    ensure_prediction_segment_annotations_for_video,
)
from endoreg_db.views.video.ai import (
    FrameAnnotationBulkUpsertView,
    FrameAnnotationRandomTaskView,
    FrameAnnotationSkipView,
    label_list,
    label_set_list,
    prediction_model_list,
    rerun_prediction_segments,
)
from endoreg_db.views.video.correction import (
    VideoApplyMaskView,
    VideoCorrectionView,
    VideoRemoveFramesView,
)
from endoreg_db.views.video.video_metadata import VideoMetadataStatsView

# Simplified Meta and Validation Endpoints

urlpatterns = [
    path(
        "media/hub/transfers/",
        HubTransferCreateView.as_view(),
        name="hub-transfer-create",
    ),
    path(
        "media/hub/transfers/<str:transfer_key>/status/",
        HubTransferStatusView.as_view(),
        name="hub-transfer-status",
    ),
    path(
        "media/hub/transfers/<str:transfer_key>/media/",
        HubTransferMediaUploadView.as_view(),
        name="hub-transfer-media-upload",
    ),
    path(
        "media/patients/<int:patient_id>/timeline/",
        PatientMediaTimelineView.as_view(),
        name="patient-media-timeline",
    ),
    path(
        "media/sensitive-media-id/<int:pk>/<str:media_type>/",
        get_sensitive_metadata_pk,
        name="sm-pk",
    ),
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
    path(
        "media/videos/<int:video_id>/frames/<int:frame_number>/stream/",
        FrameStreamView.as_view(),
        name="video-frame-stream",
    ),
    # Video Re-import API endpoint (modern media framework)
    # POST /api/media/videos/<int:pk>/reimport/
    # Re-imports a video file to regenerate metadata when OCR failed or data is incomplete
    path(
        "media/videos/<int:pk>/reimport/",
        VideoReimportView.as_view(),
        name="video-reimport",
    ),
    path(
        "media/videos/export-annotated/",
        export_annotated_data,
        name="video-annotated-export",
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
        VideoMetadataStatsView.as_view(),
        name="video-metadata",
    ),
    path(
        "media/videos/<int:pk>/fps/",
        VideoFpsView.as_view(),
        name="video-fps",
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
    path("media/videos/labels/list/", label_list, name="get_lvs_list"),
    path(
        "media/videos/label-sets/list/",
        label_set_list,
        name="video-label-set-list",
    ),
    path(
        "media/videos/prediction-models/list/",
        prediction_model_list,
        name="video-prediction-model-list",
    ),
    path(
        "media/videos/<int:pk>/segments/rerun-predictions/",
        rerun_prediction_segments,
        name="video-segments-rerun-predictions",
    ),
    path(
        "media/annotations/frames/bulk-upsert/",
        FrameAnnotationBulkUpsertView.as_view(),
        name="frame-annotations-bulk-upsert",
    ),
    path(
        "media/annotations/frames/random-task/",
        FrameAnnotationRandomTaskView.as_view(),
        name="frame-annotations-random-task",
    ),
    path(
        "media/annotations/frames/skip/",
        FrameAnnotationSkipView.as_view(),
        name="frame-annotations-skip",
    ),
    # VIDEO SEGMENT API ENDPOINTS (Modern Media Framework - October 14, 2025)
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
    # Outside-Frame Blackening API
    # POST /api/media/videos/<int:pk>/segments/blacken-outside/
    # Re-runs processed video rebuild with "outside" segments blackened.
    path(
        "media/videos/<int:pk>/segments/blacken-outside/",
        video_segments_blacken_outside,
        name="video-segments-blacken-outside",
    ),
    # Bulk Segment Mutation API
    # POST /api/media/videos/<int:pk>/segments/bulk/
    # Applies creates, updates, and deletes in one transaction.
    path(
        "media/videos/<int:pk>/segments/bulk/",
        video_segments_bulk_mutation,
        name="video-segments-bulk-mutation",
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
    path(
        "media/videos/<int:pk>/segments/import-predictions/",
        import_prediction_segments_to_manual,
        name="video-segments-import-predictions",
    ),
    path(
        "media/videos/<int:pk>/ensure-segment-annotations/",
        ensure_segment_annotations_for_video,
        name="video-segment-ensure-annotations",
    ),
    path(
        "media/videos/ensure-segment-annotations/",
        ensure_segment_annotations_bulk,
        name="video-segments-ensure-annotations",
    ),
    path(
        "media/videos/<int:pk>/ensure-prediction-segment-annotations/",
        ensure_prediction_segment_annotations_for_video,
        name="video-segment-ensure-prediction-annotations",
    ),
    path(
        "media/videos/ensure-prediction-segment-annotations/",
        ensure_prediction_segment_annotations_bulk,
        name="video-segments-ensure-prediction-annotations",
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
    path(
        "media/videos/<int:pk>/case-resolution/",
        video_case_resolution,
        name="video-case-resolution",
    ),
    # POST /api/media/videos/<pk>/sensitive-metadata/verify/
    # Update verification state (dob_verified, names_verified)
    path(
        "media/videos/<int:pk>/sensitive-metadata/verify/",
        video_sensitive_metadata_verify,
        name="video-sensitive-metadata-verify",
    ),
    # report Sensitive Metadata (Resource-Scoped)
    # GET/PATCH /api/media/pdfs/<pk>/sensitive-metadata/
    # Get or update sensitive patient data for a report
    path(
        "media/pdfs/<int:pk>/sensitive-metadata/",
        pdf_sensitive_metadata,
        name="pdf-sensitive-metadata",
    ),
    path(
        "media/pdfs/<int:pk>/case-resolution/",
        pdf_case_resolution,
        name="pdf-case-resolution",
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
    # List all sensitive metadata (combined reports and Videos)
    # Supports filtering: ?content_type=pdf|video&verified=true&search=name
    path(
        "media/sensitive-metadata/",
        sensitive_metadata_list,
        name="sensitive-metadata-list",
    ),
    # GET /api/media/pdfs/sensitive-metadata/
    # List sensitive metadata for reports only
    # Replaces legacy /api/pdf/sensitivemeta/list/
    path(
        "media/pdfs/sensitive-metadata/",
        pdf_sensitive_metadata_list,
        name="pdf-sensitive-metadata-list",
    ),
    # report media endpoints
    path("media/pdfs/", PdfMediaView.as_view(), name="pdf-list"),
    path("media/pdfs/<int:pk>/", PdfMediaView.as_view(), name="pdf-detail"),
    path(
        "media/pdfs/<int:pk>/stream/", ReportStreamView.as_view(), name="pdf-stream"
    ),  # Support ?type=raw|anonymized params
    # report Re-import API endpoint (modern media framework)
    # POST /api/media/pdfs/<int:pk>/reimport/
    # Re-imports a report file to regenerate metadata when OCR failed or data is incomplete
    path(
        "media/pdfs/<int:pk>/reimport/",
        ReportReimportView.as_view(),
        name="report-reimport",
    ),
    path(
        "media/pdfs/<int:pk>/apply-redactions/",
        PdfApplyRedactionsView.as_view(),
        name="pdf-apply-redactions",
    ),
    path(
        "media/pdfs/<int:pk>/processing-history/",
        PdfProcessingHistoryView.as_view(),
        name="pdf-processing-history",
    ),
]
# ---------------------------------------------------------------------------------------
