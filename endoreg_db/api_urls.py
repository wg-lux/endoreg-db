# Export raw API URL patterns so the project-level router can add the single `/api/` prefix.
from endoreg_db.urls import urlpatterns as api_urlpatterns

urlpatterns = api_urlpatterns
