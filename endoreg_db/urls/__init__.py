from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from endoreg_db.views import (
    ExaminationViewSet,
    FindingClassificationViewSet,
    FindingViewSet,
    PatientExaminationViewSet,
    PatientFindingViewSet,
    VideoExaminationViewSet,
    VideoViewSet,
)

from .anonymization import url_patterns as anonymization_url_patterns
from .auth import urlpatterns as auth_url_patterns
from .classification import url_patterns as classification_url_patterns
from .examination import urlpatterns as examination_url_patterns
from .files import urlpatterns as files_url_patterns
from .label_video_segment_validate import (
    url_patterns as label_video_segment_validate_url_patterns,
)
from .label_video_segments import url_patterns as label_video_segments_url_patterns

# Phase 1.2: Media Management URLs ✅ IMPLEMENTED
from .media import urlpatterns as media_url_patterns
from .patient import urlpatterns as patient_url_patterns

# TODO Phase 1.2: Implement VideoMediaView and PDFMediaView before enabling
# from .media import urlpatterns as media_url_patterns
from .report import url_patterns as report_url_patterns
from .requirements import urlpatterns as requirements_url_patterns
from .stats import url_patterns as stats_url_patterns
from .upload import urlpatterns as upload_url_patterns
from .video import url_patterns as video_url_patterns

api_urls = []
api_urls += classification_url_patterns
api_urls += anonymization_url_patterns
api_urls += auth_url_patterns
api_urls += examination_url_patterns
api_urls += files_url_patterns
api_urls += label_video_segments_url_patterns
api_urls += label_video_segment_validate_url_patterns  # Neue Validierungs-Endpunkte
# Phase 1.2: Enable media_url_patterns ✅ IMPLEMENTED
api_urls += media_url_patterns
api_urls += report_url_patterns
api_urls += upload_url_patterns
api_urls += video_url_patterns
api_urls += requirements_url_patterns
api_urls += patient_url_patterns
api_urls += stats_url_patterns

router = DefaultRouter()
router.register(r"videos", VideoViewSet, basename="videos")
router.register(r"examinations", ExaminationViewSet)
router.register(
    r"video-examinations", VideoExaminationViewSet, basename="video-examinations"
)
router.register(r"findings", FindingViewSet)
router.register(r"classifications", FindingClassificationViewSet)
router.register(r"patient-findings", PatientFindingViewSet)
router.register(r"patient-examinations", PatientExaminationViewSet)

# Additional custom video examination routes
# Frontend expects: GET /api/video/{id}/examinations/
video_examinations_list = VideoExaminationViewSet.as_view({"get": "by_video"})

# Export raw API urlpatterns (no prefix). The project-level endoreg_db/urls.py mounts these under /api/.
urlpatterns = [
    path(
        "video/<int:video_id>/examinations/",
        video_examinations_list,
        name="video-examinations-by-video",
    ),
    path("", include(api_urls)),  # Specific routes first
    path("", include(router.urls)),  # Generic router routes second
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
