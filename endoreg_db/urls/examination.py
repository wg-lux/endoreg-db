from endoreg_db.views import (
    ExaminationCreateView,
    PatientExaminationDetailView,
    ExaminationListView
)

from django.urls import path

url_patterns = [
    # NEW: Examination CRUD endpoints for SimpleExaminationForm
    # POST /api/examinations/create/ - Create new examination
    # GET /api/examinations/{id}/ - Get examination details
    # PATCH /api/examinations/{id}/ - Update examination
    # GET /api/examinations/list/ - List examinations with filtering
    path('examinations/create/', ExaminationCreateView.as_view(), name='examination_create'),
    path('examinations/<int:pk>/', ExaminationDetailView.as_view(), name='examination_detail'),
    path('examinations/list/', ExaminationListView.as_view(), name='examination_list'),
]