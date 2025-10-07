from django.urls import path

from endoreg_db.views import (
    VideoReimportView,
    SensitiveMetaDetailView,
    VideoLabelView,
    VideoStreamView,
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
]