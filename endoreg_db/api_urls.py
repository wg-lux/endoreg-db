# Export raw API URL patterns so the host router can add the main API mount.
from endoreg_db.urls import urlpatterns as api_urlpatterns

urlpatterns = api_urlpatterns
