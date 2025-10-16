from django.urls import path

from endoreg_db.views import (
    SensitiveMetaDetailView,
    VideoLabelView,
    # Note: VideoStreamView moved to modern media framework. See: endoreg_db/urls/media.py
    # Note: All Video Correction Views moved to modern media framework. See: endoreg_db/urls/media.py
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
    
    # ---------------------------------------------------------------------------------------
    # VIDEO STREAMING - MOVED TO MODERN MEDIA FRAMEWORK
    #
    # Video streaming endpoint has been migrated to the media framework
    # as of October 14, 2025. Please use the new endpoints:
    #
    # OLD → NEW:
    # GET /api/videostream/<pk>/              → GET /api/media/videos/<pk>/
    # GET /api/videostream/<pk>/              → GET /api/media/videos/<pk>/stream/
    #
    # See: endoreg_db/urls/media.py for new URL registrations
    # ---------------------------------------------------------------------------------------
    
    # Note: Video Re-import moved to modern media framework
    # See: endoreg_db/urls/media.py - POST /api/media/videos/<int:pk>/reimport/
    
    # Video Sensitive Meta endpoints (for video anonymization)
    # GET /api/video/sensitivemeta/<int:sensitive_meta_id>/
    # PATCH /api/video/sensitivemeta/<int:sensitive_meta_id>/
    path(
        'video/sensitivemeta/<int:sensitive_meta_id>/', 
        SensitiveMetaDetailView.as_view(), 
        name='video_sensitive_meta_detail'
    ),
    
    # ---------------------------------------------------------------------------------------
    # VIDEO CORRECTION API ENDPOINTS - MOVED TO MODERN MEDIA FRAMEWORK
    #
    # All video correction endpoints have been migrated to the modern media framework
    # as of October 14, 2025. Please use the new endpoints:
    #
    # OLD → NEW:
    # GET  /api/video-metadata/<id>/              → GET  /api/media/videos/<pk>/metadata/
    # GET  /api/video-processing-history/<id>/    → GET  /api/media/videos/<pk>/processing-history/
    # POST /api/video-analyze/<id>/               → POST /api/media/videos/<pk>/analyze/
    # POST /api/video-apply-mask/<id>/            → POST /api/media/videos/<pk>/apply-mask/
    # POST /api/video-remove-frames/<id>/         → POST /api/media/videos/<pk>/remove-frames/
    # POST /api/video-reprocess/<id>/             → POST /api/media/videos/<pk>/reprocess/
    #
    # See: endoreg_db/urls/media.py for new URL registrations
    # ---------------------------------------------------------------------------------------
]