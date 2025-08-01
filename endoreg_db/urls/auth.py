from django.urls import path

from endoreg_db.views import (
    public_home,
    keycloak_login,
    keycloak_callback,
    csrf_token_view,
)

urlpatterns = [
    # Authentication endpoints
    path('endoreg_db/', public_home, name='public_home'),
    path('login/', keycloak_login, name='keycloak_login'),
    path('login/callback/', keycloak_callback, name='keycloak_callback'),
    path('conf/', csrf_token_view, name='csrf_token'),
]
