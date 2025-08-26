from django.urls import path

# Prefer explicit imports to avoid relying on parent __init__ exports
from endoreg_db.views import (
    SensitiveMetaDetailView,
    SensitiveMetaVerificationView,
    SensitiveMetaListView,
)

urlpatterns = [

    # Sensitive Meta Detail API
    path(
        'pdf/sensitivemeta/<int:sensitive_meta_id>/', 
        SensitiveMetaDetailView.as_view(), 
        name='sensitive_meta_detail'
    ),
    # Alternative endpoint for query parameter access (backward compatibility)
    path(
        'pdf/sensitivemeta/', 
        SensitiveMetaDetailView.as_view(), 
        name='sensitive_meta_query'
    ),

    # Sensitive Meta Verification API
    path(
        'pdf/sensitivemeta/verify/', 
        SensitiveMetaVerificationView.as_view(), 
        name='sensitive_meta_verify'),

    # Sensitive Meta List API
    path(
        'pdf/sensitivemeta/list/', 
        SensitiveMetaListView.as_view(), 
        name='sensitive_meta_list'),
]
