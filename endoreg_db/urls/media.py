from django.urls import path

from endoreg_db.views import (
    VideoMediaView,
    PDFFileForMetaView,
    PDFStreamView,
    VideoStreamView,
)
# ---------------------------------------------------------------------------------------
# ANNOTATION API ENDPOINTS
#
# New endpoints for segment annotation management that create user-source segments
# POST /api/annotations/ - Create new annotation (creates user segment if type=segment)
# PATCH /api/annotations/<id>/ - Update annotation (creates user segment if timing/label changed)
# ---------------------------------------------------------------------------------------

# Simplified Meta and Validation Endpoints
    
    # video meta + stream
url_patterns = [
    path("media/videos/",               VideoMediaView.as_view(), name="videos-list"),
    path("media/videos/<int:pk>/",      VideoMediaView.as_view(), name="videos-detail"),
    path("media/videos/<int:video_id>/stream/", VideoStreamView.as_view(), name="video-stream"),
    # pdf meta + stream
    path("media/pdfs/",                 PDFFileForMetaView.as_view(),   name="pdfs-list"),
    path("media/pdfs/<int:pk>/",        PDFStreamView.as_view(),  name="pdfs-detail"),

]
    # ---------------------------------------------------------------------------------------
 