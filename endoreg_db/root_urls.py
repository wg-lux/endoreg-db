from django.urls import include, path

# Import raw API urlpatterns (no prefix) from the URLs package
from endoreg_db.urls import urlpatterns as api_urlpatterns

# Mount the API under /api/
urlpatterns = [
    path("api/", include((api_urlpatterns, "endoreg_db"), namespace="api")),
]
