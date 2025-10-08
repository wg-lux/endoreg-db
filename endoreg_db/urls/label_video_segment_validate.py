from django.urls import path

from endoreg_db.views.label_video_segment.validate import (
    LabelVideoSegmentValidateView,
    BulkSegmentValidateView,
    VideoSegmentValidationCompleteView
)

url_patterns = [
    # Einzelne Segment-Validierung
    # POST /api/label-video-segment/<int:segment_id>/validate/
    path(
        'label-video-segment/<int:segment_id>/validate/',
        LabelVideoSegmentValidateView.as_view(),
        name='label_video_segment_validate'
    ),
    
    # Bulk-Validierung mehrerer Segmente
    # POST /api/label-video-segments/validate-bulk/
    path(
        'label-video-segments/validate-bulk/',
        BulkSegmentValidateView.as_view(),
        name='label_video_segments_validate_bulk'
    ),
    
    # Alle Segmente eines Videos als validiert markieren
    # POST /api/videos/<int:video_id>/segments/validate-complete/
    path(
        'videos/<int:video_id>/segments/validate-complete/',
        VideoSegmentValidationCompleteView.as_view(),
        name='video_segments_validate_complete'
    ),
]
