from django.urls import path

from endoreg_db.views import (
    UploadFileView,
    UploadStatusView,
)

url_patterns = [
    # Upload endpoints
    path(
        'upload/', 
        UploadFileView.as_view(), 
        name='video_upload'
    ),
    path(
        'upload/<uuid:id>/status', 
        UploadStatusView.as_view(), 
        name='upload_status'
    ),
]