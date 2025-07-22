from django.urls import path
from endoreg_db.views import (
    get_findings_for_examination,
    get_classifications_for_finding,
    get_classification_choices,
    get_classifications_for_examination,
    ExaminationCreateView,
    PatientExaminationDetailView,
    PatientExaminationListView,
    PatientExaminationViewSet
)

url_patterns = [# URL patterns for ExaminationForm.vue API calls
    path(
        'examinations/<int:examination_id>/findings/',
        get_findings_for_examination,
        name='get_findings_for_examination'
    ),
    path(
        'findings/<int:finding_id>/classifications/',
        get_classifications_for_finding,
        name='get_classifications_for_finding'
    ),
    path(
        'classifications/<int:classification_id>/choices/',
        get_classification_choices,
        name='get_choices_for_classification'
    ),
    # NEW: Examination CRUD endpoints for SimpleExaminationForm
    # POST /api/examinations/create/ - Create new examination
    # GET /api/examinations/{id}/ - Get examination details
    # PATCH /api/examinations/{id}/ - Update examination
    # GET /api/examinations/list/ - List examinations with filtering
    #TODO Clearly Distinguish between Examination and PatientExamination

    path('examinations/create/', ExaminationCreateView.as_view(), name='examination_create'),
    path('examinations/<int:pk>/',     PatientExaminationDetailView.as_view(), name='examination_detail'),
    path('examinations/list/', PatientExaminationListView.as_view(), name='examination_list'),
    
    # NEW ENDPOINTS FOR RESTRUCTURED FRONTEND
    path(
        'examination/<int:exam_id>/classifications/',
        get_classifications_for_examination,
        name='get_classifications_for_examination'
    ),

    # URL patterns for ExaminationForm.vue API calls
    path(
        'examinations/<int:examination_id>/findings/',
        get_findings_for_examination,
        name='get_findings_for_examination'
    ),
]