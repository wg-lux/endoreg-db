# endoreg_db/urls/root_urls.py
from django.contrib import admin
from django.urls import path, include
from django.http import HttpResponse
from django.conf import settings
from django.conf.urls.static import static


def public_home(_request):
    return HttpResponse("Public home – no login required.")


urlpatterns = [
    # Public landing page
    path("", public_home, name="public_home"),
    # Django admin (optional)
    path("admin/", admin.site.urls),
    # Mount ALL API routes under /api/
    # This pulls the urlpatterns exported by endoreg_db/urls/__init__.py
    path("api/", include("endoreg_db.urls")),
    # Keycloak OIDC (mozilla-django-oidc provides /oidc/authenticate/ and /oidc/callback/)
    path("oidc/", include("mozilla_django_oidc.urls")),
]

# Serve static/media in DEBUG at the root (NOT under /api/)
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
