from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views.patient_views import (
    PatientViewSet,
    start_examination,
    get_location_choices,
    get_morphology_choices, 
)
from .views.csrf import csrf_token_view

router = DefaultRouter()
router.register(r'patients', PatientViewSet)

urlpatterns = [
    path('start-examination/', start_examination, name="start_examination"),
    path('get-location-choices/<int:location_id>/', get_location_choices, name="get_location_choices"),
    path('get-morphology-choices/<int:morphology_id>/', get_morphology_choices, name="get_morphology_choices"),
    path('api/', include(router.urls)),
    path('api/conf/', csrf_token_view, name='csrf_token'),
]
