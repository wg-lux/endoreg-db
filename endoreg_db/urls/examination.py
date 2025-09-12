from django.urls import path
from endoreg_db.views import (
    get_findings_for_examination,
    get_classifications_for_finding,
    get_classification_choices,
    get_classifications_for_examination,
    ExaminationCreateView,
    PatientExaminationDetailView,
    PatientExaminationListView,
)

urlpatterns = [# URL patterns for ExaminationForm.vue API calls
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
    # TODO: Clearly Distinguish between Examination (the template) and PatientExamination (the instance).
    # The views below handle PatientExamination instances, which represent a specific examination performed on a patient.
    # The URL names are updated to reflect this, using the 'patient_examination_*' prefix for clarity.
    # TODO: Clearly Distinguish between Examination and PatientExamination by using 'patient-examination' prefix for clarity
    path('patient-examinations/create/', ExaminationCreateView.as_view(), name='patient_examination_create'),
    path('patient-examinations/<int:pk>/',     PatientExaminationDetailView.as_view(), name='patient_examination_detail'),
    path('patient-examinations/list/', PatientExaminationListView.as_view(), name='patient_examination_list'),

    # NEW ENDPOINTS FOR RESTRUCTURED FRONTEND
    path(
        'patient-examinations/<int:exam_id>/classifications/',
        get_classifications_for_examination,
        name='get_classifications_for_examination'
    ),
    
    path(
        'patient-examinations/<int:examination_id>/findings/',
        get_findings_for_examination,
        name='get_patient_examination_findings'
    ),

]