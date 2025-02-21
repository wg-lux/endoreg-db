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
    get_all_morphology_classifications,get_all_interventions, get_patient_details
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

    #the logic for Patient Finding Intervention only consider examination where id=1, 
    # so for frondend data.find(exam => exam.id === 1); is required,
    path('api/examinations/', get_all_examinations, name='get_all_examinations'),
    
    # Finding  (fetching "colon_polyp" where id=1)
    path('api/findings/colon_polyp/', get_colon_polyp_finding, name="get_colon_polyp_finding"), #This endpoint fetches the Finding where name='colon_polyp' and id=1.  

    #Finding(location for colonoscopy default),colonoscopy_default where id = 1
    path('api/finding-location-classification/', get_finding_location_classification, name="get_finding_location_classification"),

    #For Morphology and choices
    path('api/morphology-classifications/', get_all_morphology_classifications, name='get_all_morphology_classifications'),#all morphologies
    path('api/morphology-choices/<int:classification_id>/', get_morphology_choices, name='get_morphology_choices'), #based on the selection of morphology

    #For Intervention
    path('api/interventions/', get_all_interventions, name="get_all_interventions"),

    path('api/patient-details/<int:patient_id>/', get_patient_details, name="get_patient_details"),

    # Final submit API 
    path('api/final-submit/', final_submit, name='final_submit'),


    # for example (for understanding):
    '''
    let payload = {
  patient_id: selectedPatientId,  // Patient chosen by user
  examination_id: 1,  // Always ID=1
  finding_id: 1,  // Always ID=1 (Colon Polyp)
  finding_location_classification_id: 1,  // Always ID=1 (Colonoscopy Default)
  location_choice_id: selectedLocation,  // User's selected location choice
  morphology_classification_id: selectedClassification,  // User-selected morphology classification
  morphology_choice_id: selectedChoice,  // User-selected morphology choice
  intervention_id: selectedIntervention  // User-selected intervention
};

// Sending the payload in a POST request
async function submitData() {
  try {
    let response = await fetch("http://127.0.0.1:8000/endoreg_db/api/final-submit/", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)  // Sending the payload (data)
    });
    let result = await response.json();
    console.log("Saved Successfully:", result);
  } catch (error) {
    console.error("Error saving:", error);
  }
}

    '''
]
