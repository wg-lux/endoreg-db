# endoreg_db/urls/root_urls.py
from collections.abc import Callable
from typing import cast

from django.contrib import admin
from django.urls import include, path
from django.urls.resolvers import URLPattern, URLResolver
from django.http import HttpResponse
from django.conf import settings
from requests import Request

from endoreg_db.utils.django_static import static
from endoreg_db.utils.api_urls import (
    DTYPES_API_PREFIX,
    ENDOREG_API_COMPATIBILITY_PREFIX,
    ENDOREG_API_PREFIX,
    django_path_prefix,
)
from lx_dtypes.django.api.main import api as dtypes_api

type _DtypesUrlPatterns = list[URLPattern | URLResolver]
type _DtypesUrlGetter = Callable[[], _DtypesUrlPatterns]


def public_home(_request: Request) -> HttpResponse:
    return HttpResponse("Public home – no login required.")


def _dtypes_api_urlpatterns() -> _DtypesUrlPatterns:
    get_urls: object = getattr(dtypes_api, "_get_urls", None)
    if not callable(get_urls):
        raise RuntimeError("lx_dtypes API does not expose URL patterns")
    return cast(_DtypesUrlGetter, get_urls)()


def _dtypes_api_urlconf() -> tuple[_DtypesUrlPatterns, str, str]:
    return (_dtypes_api_urlpatterns(), "ninja", "lx_dtypes_dtypes_api")


urlpatterns = [
    # Public landing page
    path("", public_home, name="public_home"),
    # Django admin (optional)
    path("admin/", admin.site.urls),
    # Canonical lx_dtypes API mount.
    path(django_path_prefix(DTYPES_API_PREFIX), _dtypes_api_urlconf()),
    # Compatibility alias retained by the upstream lx_dtypes URLConf.
    path("", include("lx_dtypes.django.urls")),
    # Canonical main API mount.
    path(django_path_prefix(ENDOREG_API_PREFIX), include("endoreg_db.urls")),
    # Compatibility alias retained for older clients during migration.
    path(
        django_path_prefix(ENDOREG_API_COMPATIBILITY_PREFIX),
        include("endoreg_db.urls"),
    ),
    # Keycloak OIDC (mozilla-django-oidc provides /oidc/authenticate/ and /oidc/callback/)
    path("oidc/", include("mozilla_django_oidc.urls")),
]

# Serve static/media in DEBUG at the root (NOT under API mounts)
if settings.DEBUG:
    protected_media_url = getattr(settings, "PROTECTED_MEDIA_URL", None)
    protected_media_root = getattr(settings, "PROTECTED_MEDIA_ROOT", None)
    if protected_media_url and protected_media_root:
        urlpatterns += static(
            protected_media_url,
            document_root=protected_media_root,
        )
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
