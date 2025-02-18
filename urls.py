from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views.patient_views import (
    PatientViewSet,
    start_examination,
    get_location_choices,
    get_morphology_choices, 
)
from .views.csrf import csrf_token_view
from .views.examination_views import ExaminationViewSet
from .views.patient_finding_intervention_views import (
    get_all_patients,
    get_all_examinations,
    get_colon_polyp_finding,  
    final_submit,get_finding_location_classification,
    get_all_morphology_classifications,get_all_interventions 
)

# Register ViewSets
router = DefaultRouter()
router.register(r'patients', PatientViewSet)
router.register(r'examinations', ExaminationViewSet)

urlpatterns = [
    # Examination-related endpoints, for built-in admin panel
    path('start-examination/', start_examination, name="start_examination"),
    path('get-location-choices/<int:location_id>/', get_location_choices, name="get_location_choices"),
    path('get-morphology-choices/<int:morphology_id>/', get_morphology_choices, name="get_morphology_choices"),
    
    # API routes
    path('api/', include(router.urls)),
    path('api/conf/', csrf_token_view, name='csrf_token'),

    # Patient & Examination endpoints
    path('api/patients/', get_all_patients, name='get_all_patients'),
    path('api/examinations/', get_all_examinations, name='get_all_examinations'),
    
    # Finding  (fetching "colon_polyp" where id=1)
    path('api/findings/colon_polyp/', get_colon_polyp_finding, name="get_colon_polyp_finding"),  

    #Finding(location for colonoscopy default)
    path('api/finding-location-classification/', get_finding_location_classification, name="get_finding_location_classification"),

    #For Morphology and choices
    path('api/morphology-classifications/', get_all_morphology_classifications, name='get_all_morphology_classifications'),
    path('api/morphology-choices/<int:classification_id>/', get_morphology_choices, name='get_morphology_choices'),

    #For Intervention
    path('api/interventions/', get_all_interventions, name = "get_all_interventions"),



    # Final submit API 
    path('api/final-submit/', final_submit, name='final_submit'),
]
