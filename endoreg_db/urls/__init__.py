from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from endoreg_db.authz.views_auth import auth_bootstrap

from endoreg_db.views import (
    ExaminationViewSet,
    FindingClassificationViewSet,
    FindingViewSet,
    PatientExaminationViewSet,
    PatientFindingViewSet,
)

from .anonymization import url_patterns as anonymization_url_patterns
from .auth import urlpatterns as auth_url_patterns
from .classification import url_patterns as classification_url_patterns
from .examination import urlpatterns as examination_url_patterns
from .media import urlpatterns as media_url_patterns
from .patient import urlpatterns as patient_url_patterns
from .requirements import urlpatterns as requirements_url_patterns
from .stats import url_patterns as stats_url_patterns
from .upload import urlpatterns as upload_url_patterns

api_urls = []
api_urls += classification_url_patterns
api_urls += anonymization_url_patterns
api_urls += auth_url_patterns
api_urls += examination_url_patterns
api_urls += media_url_patterns
api_urls += upload_url_patterns
api_urls += requirements_url_patterns
api_urls += patient_url_patterns
api_urls += stats_url_patterns

router = DefaultRouter()
router.register(r"examinations", ExaminationViewSet)
router.register(r"findings", FindingViewSet)
router.register(r"classifications", FindingClassificationViewSet)
router.register(r"patient-findings", PatientFindingViewSet)
router.register(r"patient-examinations", PatientExaminationViewSet)

# Additional custom video examination routes
# Frontend expects: GET /api/video/{id}/examinations/

# Export raw API urlpatterns (no prefix). The project-level endoreg_db/urls.py mounts these under /api/.
urlpatterns = [
    path("auth/bootstrap", auth_bootstrap, name="auth-bootstrap"),
    path("", include(router.urls)),
    path("", include(api_urls)),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
