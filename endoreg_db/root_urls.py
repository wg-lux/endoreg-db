# endoreg_db/root_urls.py
from django.urls import include, path
from django.http import HttpResponse
from django.conf import settings
from django.conf.urls.static import static

# Import raw API urlpatterns (no prefix) from your API urls package
from endoreg_db.urls import urlpatterns as api_urlpatterns


def public_home(_request):
    return HttpResponse("Public home – no login required.")


urlpatterns = [
    path("", public_home, name="public_home"),
    # path("admin/", admin.site.urls),
    # Mount ALL API endpoints under /api/
    path("api/", include((api_urlpatterns, "endoreg_db"), namespace="api")),
    # OIDC (mozilla-django-oidc provides /oidc/authenticate/ and /oidc/callback/)
    path("oidc/", include("mozilla_django_oidc.urls")),
]

# Serve static/media only in DEBUG (at root, not under /api/)
if settings.DEBUG:
    protected_media_url = getattr(settings, "PROTECTED_MEDIA_URL", None)
    protected_media_root = getattr(settings, "PROTECTED_MEDIA_ROOT", None)
    if protected_media_url and protected_media_root:
        urlpatterns += static(
            protected_media_url,
            document_root=protected_media_root,
        )
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
