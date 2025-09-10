from endoreg_db.views import (
    GenderViewSet,
    CenterViewSet,
    PatientViewSet,
)
from rest_framework.routers import DefaultRouter
from django.urls import path, include

router = DefaultRouter()
router.register(r'patients', PatientViewSet)
router.register(r'centers', CenterViewSet)
router.register(r'genders', GenderViewSet)

urlpatterns = [
    path('', include(router.urls)),
]
