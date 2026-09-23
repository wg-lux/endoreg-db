# endoreg_db/root_urls.py
from typing import cast

from django.urls import include, path
from django.urls.resolvers import URLPattern, URLResolver
from django.http import HttpRequest, HttpResponse
from django.conf import settings
from endoreg_db.utils.django_static import static
from endoreg_db.utils.api_urls import (
    DTYPES_API_PREFIX,
    ENDOREG_API_COMPATIBILITY_PREFIX,
    ENDOREG_API_PREFIX,
    django_path_prefix,
)
from lx_dtypes.django.api.main import api as dtypes_api

# Import raw API urlpatterns (no prefix) from your API urls package
from endoreg_db.urls import urlpatterns as api_urlpatterns


def public_home(_request: HttpRequest) -> HttpResponse:
    return HttpResponse("Public home – no login required.")


urlpatterns: list[URLPattern | URLResolver] = [
    path("", public_home, name="public_home"),
    # ``lx_dtypes`` owns its own Django surface. Keep this outside the main
    # endoreg API mount so reverse proxies can route dtypes-api and endoreg-api
    # independently.
    path(
        django_path_prefix(DTYPES_API_PREFIX),
        cast(tuple[list[URLPattern | URLResolver], str, str], dtypes_api.urls),
    ),
    # path("admin/", admin.site.urls),
    # Canonical main API mount.
    path(
        django_path_prefix(ENDOREG_API_PREFIX),
        include((api_urlpatterns, "endoreg_db"), namespace="api"),
    ),
    # Compatibility alias retained for older clients during migration.
    path(
        django_path_prefix(ENDOREG_API_COMPATIBILITY_PREFIX),
        include((api_urlpatterns, "endoreg_db"), namespace="api-compat"),
    ),
    # OIDC (mozilla-django-oidc provides /oidc/authenticate/ and /oidc/callback/)
    path("oidc/", include("mozilla_django_oidc.urls")),
]

# Serve static/media only in DEBUG (at root, not under API mounts)
if settings.DEBUG:
    protected_media_url = getattr(settings, "PROTECTED_MEDIA_URL", None)
    protected_media_root = getattr(settings, "PROTECTED_MEDIA_ROOT", None)
    allow_insecure_protected_media = getattr(
        settings,
        "ALLOW_INSECURE_PROTECTED_MEDIA",
        False,
    )
    if allow_insecure_protected_media and protected_media_url and protected_media_root:
        urlpatterns += static(
            protected_media_url,
            document_root=protected_media_root,
        )
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
