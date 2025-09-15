from endoreg_db.views import (
    GenderViewSet,
    CenterViewSet,
    PatientViewSet,
    PatientFindingViewSet
)
from rest_framework.routers import DefaultRouter
from django.urls import path, include

router = DefaultRouter()
router.register(r'patients', PatientViewSet)
router.register(r'centers', CenterViewSet)
router.register(r'genders', GenderViewSet)
router.register(r'patient-findings', PatientFindingViewSet)

urlpatterns = [
    path('', include(router.urls)),
    path('check_pe_exist/<int:pk>/', PatientViewSet.as_view({'get': 'check_pe_exist'}), name='check_pe_exist'),
]
