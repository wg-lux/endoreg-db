from django.urls import include, path

# Import the package-level URL patterns (defined in endoreg_db/urls/__init__.py)
from endoreg_db.urls import urlpatterns as api_urlpatterns

# Expose API under /api/
urlpatterns = [
    path("api/", include((api_urlpatterns, "endoreg_db"), namespace="api")),
]
