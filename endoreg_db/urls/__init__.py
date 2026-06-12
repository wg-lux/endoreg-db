# pyright: reportPrivateUsage=false
import logging
from importlib import reload
from collections.abc import Iterable
from typing import cast

from django.conf import settings as django_settings
from django.urls import URLPattern, URLResolver, include, path
from ninja import NinjaAPI, Router
from rest_framework.routers import DefaultRouter

from endoreg_db.authz.views_auth import auth_bootstrap
from endoreg_db.utils.django_static import static
from endoreg_db.views import PatientExaminationViewSet
from endoreg_db.views.report import patient_examination_report

from .anonymization import url_patterns as anonymization_url_patterns
from .auth import urlpatterns as auth_url_patterns
from .classification import url_patterns as _classification_url_patterns  # pyright: ignore[reportUnknownVariableType]
classification_url_patterns = cast(Iterable[object], _classification_url_patterns)
from .examination import urlpatterns as examination_url_patterns
from .media import urlpatterns as media_url_patterns
from .patient import urlpatterns as patient_url_patterns
from .settings import urlpatterns as settings_url_patterns
from .stats import url_patterns as stats_url_patterns
from .upload import urlpatterns as upload_url_patterns

logger = logging.getLogger(__name__)
NINJA_API_NAMESPACE = "endoreg-db-api"


def _typed_url_patterns(
    patterns: Iterable[object],
) -> list[URLPattern | URLResolver]:
    return [cast(URLPattern | URLResolver, pattern) for pattern in patterns]


def _patient_examination_report_router() -> Router:
    router = patient_examination_report.router
    if getattr(router, "api", None) is None:
        return router
    return cast(Router, reload(patient_examination_report).router)


def _release_ninja_api_namespace(namespace: str) -> None:
    registry = NinjaAPI._registry
    while namespace in registry:
        registry.remove(namespace)


api_urls: list[URLPattern | URLResolver] = []
api_urls += _typed_url_patterns(classification_url_patterns)
api_urls += _typed_url_patterns(anonymization_url_patterns)
api_urls += _typed_url_patterns(auth_url_patterns)
api_urls += _typed_url_patterns(examination_url_patterns)
api_urls += _typed_url_patterns(media_url_patterns)
api_urls += _typed_url_patterns(upload_url_patterns)
api_urls += _typed_url_patterns(patient_url_patterns)
api_urls += _typed_url_patterns(settings_url_patterns)
api_urls += _typed_url_patterns(stats_url_patterns)

# DRF endpoints
router = DefaultRouter()
router.register(r"patient-examinations", PatientExaminationViewSet)

# Ninja endpoints
_release_ninja_api_namespace(NINJA_API_NAMESPACE)
ninja_api = NinjaAPI(
    title="Endoreg DB API",
    version="1.0.0",
    urls_namespace=NINJA_API_NAMESPACE,
)

ninja_api.add_router(
    "/patient-examination-reports",
    _patient_examination_report_router(),
)

urlpatterns: list[URLPattern | URLResolver] = [
    path("auth/bootstrap", auth_bootstrap, name="auth-bootstrap"),

    # Existing non-router URL modules
    path("", include(api_urls)),

    # Ninja API routes
    path("", ninja_api.urls),

    # DRF ViewSet routes
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
