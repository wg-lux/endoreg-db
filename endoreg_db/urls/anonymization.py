from django.urls import path
from endoreg_db.views import (
    anonymization_overview,
    start_anonymization,
    anonymization_current,
    anonymization_status,
    validate_anonymization,
)

url_patterns = [
    # URL patterns for anonymization overview
    path('anonymization/items/overview/', anonymization_overview, name='anonymization_items_overview'),
    path('anonymization/<int:file_id>/current/', anonymization_current, name='set_current_for_validation'),
    path('anonymization/<int:file_id>/start/', start_anonymization, name='start_anonymization'),
    path('anonymization/<int:file_id>/status/', anonymization_status, name='get_anonymization_status'),
    path('anonymization/<int:file_id>/validate/', validate_anonymization, name='validate_anonymization'),
]