from django.urls import path

from endoreg_db.views import (
    VideoReimportView,
    SensitiveMetaDetailView,
)

   
url_patterns = [
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