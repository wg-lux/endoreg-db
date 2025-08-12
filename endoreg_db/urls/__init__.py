
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from rest_framework.routers import DefaultRouter

from endoreg_db.views import (
    PatientViewSet,
    GenderViewSet,
    CenterViewSet,
    VideoViewSet,
    ExaminationViewSet,
    VideoExaminationViewSet,
    FindingViewSet,
    FindingClassificationViewSet, 
    PatientFindingViewSet,
)

from .anonymization import url_patterns as anonymization_url_patterns
from .classification import url_patterns as classification_url_patterns
from .auth import urlpatterns as auth_url_patterns
from .examination import urlpatterns as examination_url_patterns
from .files import urlpatterns as files_url_patterns
from .label_video_segments import url_patterns as label_video_segments_url_patterns
from .media import urlpatterns as media_url_patterns
from .pdf import urlpatterns as pdf_url_patterns
from .report import url_patterns as report_url_patterns
from .upload import urlpatterns as upload_url_patterns
from .video import url_patterns as video_url_patterns

api_urls = []
api_urls += classification_url_patterns
api_urls += anonymization_url_patterns
api_urls += auth_url_patterns
api_urls += examination_url_patterns
api_urls += files_url_patterns
api_urls += label_video_segments_url_patterns
api_urls += media_url_patterns
api_urls += pdf_url_patterns
api_urls += report_url_patterns
api_urls += upload_url_patterns
api_urls += video_url_patterns

router = DefaultRouter()
router.register(r'patients', PatientViewSet)
router.register(r'genders', GenderViewSet)
router.register(r'centers', CenterViewSet)
router.register(r'videos', VideoViewSet, basename='videos')  
router.register(r'examinations', ExaminationViewSet)
router.register(r'video-examinations', VideoExaminationViewSet, basename='video-examinations')  # NEW: Video examination CRUD
# Add new router registrations
router.register(r'findings', FindingViewSet)
router.register(r'classifications', FindingClassificationViewSet)
router.register(r'patient-findings', PatientFindingViewSet)
# router.register(r'patient-examinations', PatientExaminationViewSet)



urlpatterns = [
    path('', include(router.urls)),  
    path('', include(api_urls))
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

