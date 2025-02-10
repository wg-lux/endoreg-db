from django.urls import path
from .views.patient_views import (
    start_examination,
    get_location_choices,
    get_morphology_choices,  
)

urlpatterns = [
    path('start-examination/', start_examination, name="start_examination"),
    path('get-location-choices/<int:location_id>/', get_location_choices, name="get_location_choices"),
    path('get-morphology-choices/<int:morphology_id>/', get_morphology_choices, name="get_morphology_choices"),  
]
