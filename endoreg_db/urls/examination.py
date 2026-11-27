from django.urls import path
from endoreg_db.views.examination.get_indications import (
    get_indication_choices,
    get_indications_for_examination,
)
from endoreg_db.views.examination.get_interventions import (
    get_interventions_for_examination,
    get_interventions_for_finding,
)
from endoreg_db.views import (
    ExaminationCreateView,
    PatientExaminationDetailView,
    PatientExaminationListView,
)

urlpatterns = [
    # TODO: Clearly Distinguish between Examination (the template) and PatientExamination (the instance).
    # The views below handle PatientExamination instances, which represent a specific examination performed on a patient.
    # The URL names are updated to reflect this, using the 'patient_examination_*' prefix for clarity.
    # TODO: Clearly Distinguish between Examination and PatientExamination by using 'patient-examination' prefix for clarity
    path(
        "patient-examinations/create/",
        ExaminationCreateView.as_view(),
        name="patient_examination_create",
    ),
    path(
        "patient-examinations/<int:pk>/",
        PatientExaminationDetailView.as_view(),
        name="patient_examination_detail",
    ),
    path(
        "patient-examinations/list/",
        PatientExaminationListView.as_view(),
        name="patient_examination_list",
    ),
    path(
        "examinations/<int:exam_id>/indications/",
        get_indications_for_examination,
        name="examination_indications",
    ),
    path(
        "indications/<int:indication_id>/choices/",
        get_indication_choices,
        name="indication_choices",
    ),
    path(
        "examinations/<int:exam_id>/interventions/",
        get_interventions_for_examination,
        name="examination_interventions",
    ),
    path(
        "examinations/<int:exam_id>/findings/<int:finding_id>/interventions/",
        get_interventions_for_finding,
        name="examination_finding_interventions",
    ),
]
