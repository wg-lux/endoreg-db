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
    path('check_pe_exist/<int:pk>/', PatientViewSet.as_view({'get': 'check_pe_exist'}), name='check_pe_exist'),
    path('get_patient_examination/<int:pk>/', PatientViewSet.as_view({'get': 'get_patient_examination'}), name='get_patient_examination'),
    path('list_patient_examinations/', PatientViewSet.as_view({'get': 'list_patient_examinations'}), name='list_patient_examinations'),
]
