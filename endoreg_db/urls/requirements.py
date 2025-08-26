from django.urls import path, include
from rest_framework.routers import DefaultRouter
from endoreg_db.views.requirement_lookup.lookup import LookupViewSet


router = DefaultRouter()
router.register(r"lookup", LookupViewSet, basename="lookup")

urlpatterns = [
    path("", include(router.urls)),
]