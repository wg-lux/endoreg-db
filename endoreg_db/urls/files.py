from django.urls import path
from endoreg_db.views import AvailableFilesListView

url_patterns = [
    path('available-files/', 
    AvailableFilesListView.as_view(), 
    name='available_files_list'),
]