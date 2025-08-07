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
    
urlpatterns = [
    # Video media endpoints
    path("media/videos/", VideoMediaView.as_view(), name="video-list"),
    path("media/videos/<int:pk>/", VideoMediaView.as_view(), name="video-detail"),
    path("media/videos/<int:pk>/stream/", VideoStreamView.as_view(), name="video-stream"),

    # PDF media endpoints
    path("media/pdfs/", PDFFileForMetaView.as_view(), name="pdf-list"),
    path("media/pdfs/<int:pk>/", PDFFileForMetaView.as_view(), name="pdf-detail"),
    path("media/pdfs/<int:pk>/stream/", PDFStreamView.as_view(), name="pdf-stream"),
]
    # ---------------------------------------------------------------------------------------
