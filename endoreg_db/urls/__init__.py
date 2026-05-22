import logging
from typing import cast
from django.conf import settings as django_settings
from django.urls import URLResolver, URLPattern, include, path
from rest_framework.routers import DefaultRouter
from endoreg_db.utils.web.django_static import static

logger = logging.getLogger(__name__)

# ``lx_dtypes`` is a declared package dependency (``lx-dtypes`` in
# pyproject.toml). Keep URLConf imports on that package boundary instead of
# shadowing it with the checked-out ``lx-data-models`` tree via sys.path.

from endoreg_db.authz.views_auth import auth_bootstrap

from endoreg_db.views import (
    ExaminationViewSet,
    FindingClassificationViewSet,
    FindingViewSet,
    PatientExaminationViewSet,
    PatientExaminationReportViewSet,
    PatientFindingViewSet,
)

from .anonymization import url_patterns as anonymization_url_patterns
from .auth import urlpatterns as auth_url_patterns
from .classification import url_patterns as classification_url_patterns
from .examination import urlpatterns as examination_url_patterns
from .media import urlpatterns as media_url_patterns
from .patient import urlpatterns as patient_url_patterns
from .settings import urlpatterns as settings_url_patterns
from .stats import url_patterns as stats_url_patterns
from .upload import urlpatterns as upload_url_patterns

api_urls = []
api_urls += classification_url_patterns
api_urls += anonymization_url_patterns
api_urls += auth_url_patterns
api_urls += examination_url_patterns
api_urls += media_url_patterns
api_urls += upload_url_patterns
api_urls += patient_url_patterns
api_urls += settings_url_patterns
api_urls += stats_url_patterns

router = DefaultRouter()
router.register(r"examinations", ExaminationViewSet)
router.register(r"findings", FindingViewSet)
router.register(r"classifications", FindingClassificationViewSet)
router.register(r"patient-findings", PatientFindingViewSet)
router.register(r"patient-examinations", PatientExaminationViewSet)
router.register(r"patient-examination-reports", PatientExaminationReportViewSet)

# Additional custom video examination routes
# Frontend expects: GET /api/video/{id}/examinations/

# Export raw API urlpatterns (no prefix). The project-level endoreg_db/urls.py mounts these under /api/.
urlpatterns: list[URLPattern | URLResolver] = [
    path("auth/bootstrap", auth_bootstrap, name="auth-bootstrap"),
    path("", include(api_urls)),
    path("", include(router.urls)),
]

if django_settings.DEBUG:
    media_url = cast(str | None, getattr(django_settings, "PROTECTED_MEDIA_URL", None))
    media_root = getattr(django_settings, "PROTECTED_MEDIA_ROOT", None)
    static_url = cast(str | None, getattr(django_settings, "STATIC_URL", None))
    if media_url and media_root:
        urlpatterns += static(media_url, document_root=media_root)
    if static_url:
        urlpatterns += static(static_url, document_root=django_settings.STATIC_ROOT)
