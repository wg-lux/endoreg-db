from django.urls import path
from endoreg_db.views.anonymization import (
    AnonymizationOverviewView,
    AnonymizationValidateView,
    start_anonymization,
    anonymization_current,
    anonymization_status,
    polling_coordinator_info,
    clear_processing_locks,
    has_raw_video_file,
)
from endoreg_db.views.anonymization import media_management

url_patterns = [
    # URL patterns for anonymization overview
    path('anonymization/items/overview/', AnonymizationOverviewView.as_view(), name='anonymization_items_overview'),
    path('anonymization/<int:file_id>/current/', anonymization_current, name='set_current_for_validation'),
    path('anonymization/<int:file_id>/start/', start_anonymization, name='start_anonymization'),
    path('anonymization/<int:file_id>/status/', anonymization_status, name='get_anonymization_status'),
    path('anonymization/<int:file_id>/validate/', AnonymizationValidateView.as_view(), name='validate_anonymization'),
        # Polling Coordination API (new endpoints)
    path('anonymization/polling-info/', polling_coordinator_info, name='polling_coordinator_info'),
    path('anonymization/clear-locks/', clear_processing_locks, name='clear_processing_locks'),
    path('anonymization/<int:file_id>/has-raw/', has_raw_video_file, name='has_raw_video_file'),

    
    # Media Management API (new endpoints)
    path('media-management/status/', media_management.MediaManagementView.as_view(), name='media_management_status'),
    path('media-management/cleanup/', media_management.MediaManagementView.as_view(), name='media_management_cleanup'),
    path('media-management/force-remove/<int:file_id>/', media_management.force_remove_media, name='force_remove_media'),
    path('media-management/reset-status/<int:file_id>/', media_management.reset_processing_status, name='reset_processing_status'),
]
