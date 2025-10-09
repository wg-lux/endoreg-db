from django.urls import path

from endoreg_db.views.media import (
    VideoMediaView,
    PDFMediaManagementView as PDFMediaView,  # Alias to avoid conflict with legacy pdf.PDFMediaView
)
from endoreg_db.views import (
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
    path("media/videos/<int:pk>/", VideoStreamView.as_view(), name="video-detail-stream"),  # Support ?type= params
    path("media/videos/<int:pk>/details/", VideoMediaView.as_view(), name="video-detail"),  # JSON metadata
    path("media/videos/<int:pk>/stream/", VideoStreamView.as_view(), name="video-stream"),  # Legacy support

    # PDF media endpoints
    path("media/pdfs/", PDFMediaView.as_view(), name="pdf-list"),
    path("media/pdfs/<int:pk>/", PDFMediaView.as_view(), name="pdf-detail"),
    path("media/pdfs/<int:pk>/stream/", PDFMediaView.as_view(), name="pdf-stream"),
]
    # ---------------------------------------------------------------------------------------
