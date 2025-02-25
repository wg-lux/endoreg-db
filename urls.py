from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views.patient_views import (
    PatientViewSet,
    start_examination,
    get_location_choices,
)
from .views.csrf import csrf_token_view
from .views.examination_views import ExaminationViewSet
from .views.patient_finding_intervention_views import (
    get_all_patients,
    get_all_examinations,
    get_colon_polyp_finding,  
    final_submit,get_finding_location_classification,
    get_all_morphology_classifications,get_all_interventions, get_patient_details,get_morphology_choices,get_patient_details
)

# Register ViewSets
router = DefaultRouter()
router.register(r'patients', PatientViewSet)
router.register(r'examinations', ExaminationViewSet)

urlpatterns = [
    # Examination-related endpoints, for built-in admin panel
    path('start-examination/', start_examination, name="start_examination"),
    path('get-location-choices/<int:location_id>/', get_location_choices, name="get_location_choices"),
    #path('get-morphology-choices/<int:morphology_id>/', get_morphology_choices, name="get_morphology_choices"),
    
    # API routes
    path('api/', include(router.urls)),
    path('api/conf/', csrf_token_view, name='csrf_token'),

    #THESES ARE THE ROUTES FOR PATIENT FINDING INTERVENTION
    # Patient & Examination endpoints
    path('api/patients/', get_all_patients, name='get_all_patients'),

    #the logic for Patient Finding Intervention only consider examination where id=1, 
    # so for frontend data.find(exam => exam.id === 1); is required,
    path('api/examinations/', get_all_examinations, name='get_all_examinations'),#http://127.0.0.1:8000/endoreg_db/api/examinations/1/
    
    # Finding  (fetching "colon_polyp" where id=1)
    #http://127.0.0.1:8000/endoreg_db/api/findings/colon_polyp/
    path('api/findings/colon_polyp/', get_colon_polyp_finding, name="get_colon_polyp_finding"), #This endpoint fetches the Finding where name='colon_polyp' and id=1.  

    #Finding(location for colonoscopy default),colonoscopy_default where id = 1
    #http://127.0.0.1:8000/endoreg_db/api/finding-location-classification/
    #and it return the data like:
    #{
    #"id": 1,
    #"name": "colonoscopy_default",
    #"choices": [
    #    {
    #        "id": 8,
    #        "name": "rectum"
    #    },
    #    {
    #        "id": 9,
    #        "name": "anal_canal"
    #    },
    #    {
    #        "id": 1,
    #        "name": "terminal_ileum"
    #    },]}
    path('api/finding-location-classification/', get_finding_location_classification, name="get_finding_location_classification"),

    #For Morphology and choices,http://127.0.0.1:8000/endoreg_db/api/morphology-classifications/
    path('api/morphology-classifications/', get_all_morphology_classifications, name='get_all_morphology_classifications'),#all morphologies
    #path('api/morphology-choices/<int:classification_id>/', get_morphology_choices, name='get_morphology_choices'), #based on the selection of morphology
    #e.g. http://127.0.0.1:8000/endoreg_db/api/morphology-choices/1/
    path('api/morphology-choices/<int:classification_id>/', get_morphology_choices, name='get_morphology_choices'),

    #For Intervention
    #http://127.0.0.1:8000/endoreg_db/api/interventions/
    path('api/interventions/', get_all_interventions, name="get_all_interventions"),

    #e.g:http://127.0.0.1:8000/endoreg_db/api/patient-details/7/
    path('api/patient-details/<int:patient_id>/', get_patient_details, name="get_patient_details"),

    # Final submit API 
    path('api/final-submit/', final_submit, name='final_submit'),

]
