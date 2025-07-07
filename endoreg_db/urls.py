from .views.csrf import csrf_token_view
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from rest_framework.routers import DefaultRouter
from .views.patient_views import (
    PatientViewSet,
    GenderViewSet,
    CenterViewSet,
    start_examination,
    get_location_choices,
    get_morphology_choices,     
)
from .views.Frames_NICE_and_PARIS_classifications_views import (
    ForNiceClassificationView, 
    ForParisClassificationView,
    BatchClassificationView,
    ClassificationStatusView
)
# endoreg_db_production/endoreg_db/urls.py
from .views.keycloak_views import keycloak_login, keycloak_callback, public_home
#from .views.feature_selection_view import FetchSingleFramePredictionView // its implemented in endo-ai other project need to add here
from .views.video_segmentation_views import VideoViewSet, VideoView, VideoLabelView, UpdateLabelSegmentsView, VideoStreamView
from .views.views_for_timeline import video_timeline_view
from .views.raw_video_meta_validation_views import VideoFileForMetaView
from .views.raw_pdf_meta_validation_views import PDFFileForMetaView
from .views.raw_pdf_meta_validation_views import UpdateSensitiveMetaView
from .views.raw_pdf_anony_text_validation_views import RawPdfAnonyTextView, UpdateAnonymizedTextView
from .views.examination_views import (
    ExaminationViewSet,
    get_morphology_classification_choices_for_exam,
    get_location_classification_choices_for_exam,
    get_interventions_for_exam,
    get_instruments_for_exam,
    # New imports for restructured frontend
    get_location_classifications_for_exam,
    get_findings_for_exam,
    get_location_choices_for_classification,
    get_interventions_for_finding,
    # Import for video examinations
    get_examinations_for_video,
    # NEW: Add the missing API endpoints for ExaminationForm.vue
    get_findings_for_examination,
    get_location_classifications_for_finding,
    get_morphology_classifications_for_finding,
    get_choices_for_location_classification,
    get_choices_for_morphology_classification,
)

# Add new imports for missing endpoints
from .views.finding_views import FindingViewSet
from .views.classification_views import (
    LocationClassificationViewSet,
    MorphologyClassificationViewSet
)
from .views.patient_finding_views import (
    PatientFindingViewSet,
    create_patient_finding_location,
    create_patient_finding_morphology
)
from .views.patient_examination_views import PatientExaminationViewSet

# NEW: Import Stats Views
from .views.stats_views import (
    ExaminationStatsView,
    VideoSegmentStatsView,
    SensitiveMetaStatsView,
    GeneralStatsView
)

from .views.label_segment_views import video_segments_view, video_segment_detail_view
from .views.report_service_views import (
    ReportListView,
    ReportWithSecureUrlView,
    SecureFileUrlView, 
    ReportFileMetadataView,
    SecureFileServingView,
    validate_secure_url
)

from .views.upload_views import (
    UploadFileView,
    UploadStatusView
)

# Add sensitive meta views import
from .views.sensitive_meta_views import (
    SensitiveMetaDetailView,
    SensitiveMetaVerificationView,
    SensitiveMetaListView,
    AvailableFilesListView
)

# Add missing anonymization overview imports
from .views.anonymization_overview import (
    anonymization_items_overview,
    start_anonymization,
    get_anonymization_status
)

# Import the correct version from anonymization_overview.py
from .views.anonymization_overview import set_current_for_validation

# Add missing video anonymization imports
from .views.video_anonymization_views import (
    anonymize_video,
    anonymization_status,
    anonymization_batch_status,
    anonymization_batch_start
)

# Move stats endpoint BEFORE router to avoid conflicts
urlpatterns = [
    # STATISTICS API ENDPOINTS (moved to top to avoid router conflicts)
    # Examination Statistics API
    # GET /api/examinations/stats/
    # Liefert Statistiken über Examinations und PatientExaminations
    path('examinations/stats/', ExaminationStatsView.as_view(), name='examination_stats'),
]

router = DefaultRouter()
router.register(r'patients', PatientViewSet)
router.register(r'genders', GenderViewSet)
router.register(r'centers', CenterViewSet)
router.register(r'videos', VideoViewSet, basename='videos')  # New: separate JSON endpoints
router.register(r'examinations', ExaminationViewSet)
# Add new router registrations
router.register(r'findings', FindingViewSet)
router.register(r'location-classifications', LocationClassificationViewSet)
router.register(r'morphology-classifications', MorphologyClassificationViewSet)
router.register(r'patient-findings', PatientFindingViewSet)
router.register(r'patient-examinations', PatientExaminationViewSet)

urlpatterns += [
    path('', include(router.urls)),  # This creates /api/videos/ and /api/videos/<id>/ endpoints
    
    # NEW: Label Video Segment API endpoints
    path('video-segments/', video_segments_view, name='video_segments'),
    path('video-segments/<int:segment_id>/', video_segment_detail_view, name='video_segment_detail'),

    path('upload/', UploadFileView.as_view(), name='video_upload'),  #Upload endpoint
    path('upload/<uuid:id>/status', UploadStatusView.as_view(), name='upload_status'),  # Status endpoint for polling
    # ---------------------------------------------------------------------------------------
    # CLASSIFICATION API ENDPOINTS
    #
    # Diese Endpunkte führen automatische Polyp-Klassifikationen durch:
    # - NICE: Für digitale Chromoendoskopie/NBI-basierte Klassifikation
    # - PARIS: Für Standard-Weißlicht-Klassifikation
    # 
    # Diese APIs sind für Backend-Verarbeitung gedacht und werden typischerweise
    # nach dem Import eines Videos automatisch aufgerufen.
    # ---------------------------------------------------------------------------------------
    
    # NICE Classification API
    # POST /api/classifications/nice/
    # Body: {"video_ids": [1, 2, 3]} oder leerer Body für alle Videos
    # Führt NICE-Klassifikation für spezifizierte Videos durch
    path('classifications/nice/', ForNiceClassificationView.as_view(), name='nice_classification'),
    
    # PARIS Classification API  
    # POST /api/classifications/paris/
    # Body: {"video_ids": [1, 2, 3]} oder leerer Body für alle Videos
    # Führt PARIS-Klassifikation für spezifizierte Videos durch
    path('classifications/paris/', ForParisClassificationView.as_view(), name='paris_classification'),
    
    # Batch Classification API (beide Typen)
    # POST /api/classifications/batch/
    # Body: {"video_ids": [1, 2, 3], "types": ["nice", "paris"]}
    # Führt beide Klassifikationstypen für spezifizierte Videos durch
    path('classifications/batch/', BatchClassificationView.as_view(), name='batch_classification'),
    
    # Classification Status API
    # GET /api/classifications/status/<video_id>/
    # Gibt den Status der Klassifikationen für ein Video zurück
    path('classifications/status/<int:video_id>/', ClassificationStatusView.as_view(), name='classification_status'),
    
    # ---------------------------------------------------------------------------------------

    # ORIGINAL ENDPOINTS USED BY SimpleExaminationForm - KEEPING FOR COMPATIBILITY
    path('start-examination/', start_examination, name="start_examination"),
    path('get-location-choices/<int:location_id>/', get_location_choices, name="get_location_choices"),
    path('get-morphology-choices/<int:morphology_id>/', get_morphology_choices, name="get_morphology_choices"),
    path('examinations/', ExaminationViewSet.as_view({'get': 'list'}), name='examination-list'),
    
    # NEW ENDPOINTS FOR RESTRUCTURED FRONTEND
    path(
        'examination/<int:exam_id>/location-classifications/',
        get_location_classifications_for_exam,
        name='get_location_classifications_for_exam'
    ),
    path(
        'examination/<int:exam_id>/findings/',
        get_findings_for_exam,
        name='get_findings_for_exam'
    ),
    path(
        'examination/<int:exam_id>/location-classification/<int:location_classification_id>/choices/',
        get_location_choices_for_classification,
        name='get_location_choices_for_classification'
    ),
    path(
        'examination/<int:exam_id>/finding/<int:finding_id>/interventions/',
        get_interventions_for_finding,
        name='get_interventions_for_finding'
    ),
    
    # URL patterns for anonymization overview
    path('anonymization/items/overview/', anonymization_items_overview, name='anonymization_items_overview'),
    path('anonymization/<int:file_id>/current/', set_current_for_validation, name='set_current_for_validation'),
    path('anonymization/<int:file_id>/start/', start_anonymization, name='start_anonymization'),
    path('anonymization/<int:file_id>/status/', get_anonymization_status, name='get_anonymization_status'),
    
    # Video anonymization API endpoints
    path('videos/<uuid:video_id>/anonymize/', anonymize_video, name='anonymize_video'),
    path('videos/<uuid:video_id>/anonymization-status/', anonymization_status, name='anonymization_status'),
    path('videos/anonymization-batch-status/', anonymization_batch_status, name='anonymization_batch_status'),
    path('videos/anonymization-batch-start/', anonymization_batch_start, name='anonymization_batch_start'),
    
    # URL patterns for ExaminationForm.vue API calls
    path(
        'examinations/<int:examination_id>/findings/',
        get_findings_for_examination,
        name='get_findings_for_examination'
    ),
    path(
        'findings/<int:finding_id>/location-classifications/',
        get_location_classifications_for_finding,
        name='get_location_classifications_for_finding'
    ),
    path(
        'findings/<int:finding_id>/morphology-classifications/',
        get_morphology_classifications_for_finding,
        name='get_morphology_classifications_for_finding'
    ),
    path(
        'location-classifications/<int:classification_id>/choices/',
        get_choices_for_location_classification,
        name='get_choices_for_location_classification'
    ),
    path(
        'morphology-classifications/<int:classification_id>/choices/',
        get_choices_for_morphology_classification,
        name='get_choices_for_morphology_classification'
    ),

    # EXISTING ENDPOINTS (KEEPING FOR BACKWARD COMPATIBILITY)
    path(
        'examination/<int:exam_id>/morphology-classification-choices/',
        get_morphology_classification_choices_for_exam,
        name='get_morphology_classification_choices_for_exam'
    ),
    path(
        'examination/<int:exam_id>/location-classification-choices/',
        get_location_classification_choices_for_exam,
        name='get_location_classification_choices_for_exam'
    ),
    path(
        'examination/<int:exam_id>/interventions/',
        get_interventions_for_exam,
        name='get_interventions_for_exam'
    ),
    path(
        'examination/<int:exam_id>/instruments/',
        get_instruments_for_exam,
        name='get_instruments_for_exam'
    ),
    path('conf/', csrf_token_view, name='csrf_token'),

    # VIDEO STREAMING ENDPOINTS - Raw bytes only
    # /api/videos/<id>/stream/  → Raw video file streaming (automatically created by router)
    # Note: The stream action is automatically available through the VideoViewSet router registration

    path('videos/', VideoView.as_view(), name='video-list-legacy'),
    path('videos/<int:video_id>/', VideoView.as_view(), name='video-detail-legacy'),
    path('videostream/<int:video_id>/', VideoStreamView.as_view(), name='video_stream'),
    
    # Video label endpoints for backward compatibility
    path("video/<int:video_id>/label/<str:label_name>/", VideoLabelView.as_view(), name="video_label_times"),
    path("video/<int:video_id>/label/<int:label_id>/update_segments/", UpdateLabelSegmentsView.as_view(), name="update_label_segments"),
    path("video/<int:video_id>/examinations/", get_examinations_for_video, name="video_examinations"),

    # Authentication endpoints
    path('endoreg_db/', public_home, name='public_home'),
    path('login/', keycloak_login, name='keycloak_login'),
    path('login/callback/', keycloak_callback, name='keycloak_callback'),


    #----------------------------------START : SENSITIVE META AND RAWVIDEOFILE VIDEO PATIENT DETAILS-------------------------------

    # API Endpoint for fetching video metadata or streaming the next available video
    # This endpoint is used by the frontend to fetch:
    #  - The first available video if `last_id` is NOT provided.
    #  - The next available video where `id > last_id` if `last_id` is provided.
    #  - If `Accept: application/json` is set in headers, it returns video metadata as JSON.
    #  - If no videos are available, it returns {"error": "No more videos available."}.
    #  const url = lastId ? `http://localhost:8000/video/meta/?last_id=${lastId}` : "http://localhost:8000/video/meta/";
    path("video/sensitivemeta/", VideoFileForMetaView.as_view(), name="video_meta"),  # Single endpoint for both first and next video    



    # This API endpoint allows updating specific patient details (SensitiveMeta)
    # linked to a video. It is used to correct or modify the patient's first name,
    # last name, date of birth, and examination date.
    # Fetch video metadata and update patient details
    # The frontend should send a JSON request body like this:
    # {
    #     "sensitive_meta_id": 2,          # The ID of the SensitiveMeta entry (REQUIRED)
    #     "patient_first_name": "John",    # New first name (REQUIRED, cannot be empty)
    #     "patient_last_name": "Doe",      # New last name (REQUIRED, cannot be empty)
    #     "patient_dob": "1985-06-15",     # New Date of Birth (REQUIRED, format YYYY-MM-DD)
    #     "examination_date": "2024-03-20" # New Examination Date (OPTIONAL, format YYYY-MM-DD)
    # }
    # - The frontend sends a PATCH request to this endpoint with updated patient data.
    # - The backend validates the input using the serializer (`SensitiveMetaUpdateSerializer`).
    # - If validation passes, the patient information is updated in the database.
    # - If there are errors (e.g., missing fields, incorrect date format), 
    #   the API returns structured error messages.
    path("video/update_sensitivemeta/", VideoFileForMetaView.as_view(), name="update_patient_meta"),


        
        
#----------------------------------START : SENSITIVE META AND RAWPDFOFILE PDF PATIENT DETAILS-------------------------------
        
    # Sensitive Meta Detail API (moved before generic pdf/sensitivemeta/ to avoid conflicts)
    # GET /api/pdf/sensitivemeta/<int:sensitive_meta_id>/
    # PATCH /api/pdf/sensitivemeta/<int:sensitive_meta_id>/
    # Used by anonymization store to fetch and update sensitive meta details
    path('pdf/sensitivemeta/<int:sensitive_meta_id>/', 
         SensitiveMetaDetailView.as_view(), 
         name='sensitive_meta_detail'),
    
    # Alternative endpoint for query parameter access (backward compatibility)
    # GET /api/pdf/sensitivemeta/?id=<sensitive_meta_id>
    path('pdf/sensitivemeta/', 
         SensitiveMetaDetailView.as_view(), 
         name='sensitive_meta_query'),
    
    #The first request (without id) fetches the first available PDF metadata.
    #The "Next" button (with id) fetches the next available PDF.
    #If an id is provided, the API returns the actual PDF file instead of JSON.
    # RENAMED to avoid conflict with new SensitiveMetaDetailView
    path("pdf/meta/", PDFFileForMetaView.as_view(), name="pdf_meta"),  




    # This API endpoint allows updating specific patient details (SensitiveMeta)
    # linked to a PDF record. It enables modifying the patient's first name,
    # last name, date of birth, and examination date.

    # The frontend should send a JSON request body like this:
    # {
    #     "sensitive_meta_id": 2,          # The ID of the SensitiveMeta entry (REQUIRED)
    #     "patient_first_name": "John",    # New first name (OPTIONAL, if provided, cannot be empty)
    #     "patient_last_name": "Doe",      # New last name (OPTIONAL, if provided, cannot be empty)
    #     "patient_dob": "1985-06-15",     # New Date of Birth (OPTIONAL, format YYYY-MM-DD)
    #     "examination_date": "2024-03-20" # New Examination Date (OPTIONAL, format YYYY-MM-DD)
    # }

    # - The frontend sends a PATCH request to this endpoint with the updated patient data.
    # - The backend processes the request and updates only the fields that are provided.
    # - If validation passes, the corresponding SensitiveMeta entry is updated in the database.
    # - If errors occur (e.g., invalid ID, empty fields, incorrect date format), 
    #   the API returns structured error messages.

    path("pdf/update_sensitivemeta/", UpdateSensitiveMetaView.as_view(), name="update_pdf_meta"),





    #  API Endpoint for Fetching PDF Data (Including Anonymized Text)
    # - This endpoint is used when the page loads.
    # - Fetches the first available PDF (if no `last_id` is provided).
    # - If `last_id` is given, fetches the next available PDF.
    # - The frontend calls this endpoint on **page load** and when clicking the **next button**.
    # Example frontend usage:
    #     const url = lastId ? `http://localhost:8000/pdf/anony_text/?last_id=${lastId}` 
    #                        : "http://localhost:8000/pdf/anony_text/";
    path("pdf/anony_text/", RawPdfAnonyTextView.as_view(), name="pdf_anony_text"),  

    #  API Endpoint for Updating the `anonymized_text` Field in `RawPdfFile`
    # - This endpoint is called when the user edits the anonymized text and clicks **Save**.
    # - Updates only the `anonymized_text` field for the specified PDF `id`.
    # - The frontend sends a **PATCH request** to this endpoint with the updated text.
    # Example frontend usage:
    #     axios.patch("http://localhost:8000/pdf/update_anony_text/", { id: 1, anonymized_text: "Updated text" });
    path("pdf/update_anony_text/", UpdateAnonymizedTextView.as_view(), name="update_pdf_anony_text"),



    # ---------------------------------------------------------------------------------------
    # REPORT SERVICE ENDPOINTS
    #
    # Neue API-Endpunkte für den Report-Service mit sicheren URLs
    #
    # Diese Endpunkte ermöglichen es dem Frontend (UniversalReportViewer), 
    # Reports mit zeitlich begrenzten, sicheren URLs zu laden und anzuzeigen.
    #
    # Verwendung im Frontend:
    #   - loadReportWithSecureUrl(reportId) 
    #   - generateSecureUrl(reportId, fileType)
    #   - validateCurrentUrl()
    #
    # ---------------------------------------------------------------------------------------

    # API-Endpunkt für paginierte Report-Listen mit Filterung
    # GET /api/reports/?page=1&page_size=20&status=pending&file_type=pdf&patient_name=John
    # Lädt eine paginierte Liste aller Reports mit optionalen Filtern
    path('reports/', 
         ReportListView.as_view(), 
         name='report_list'),

    # API-Endpunkt für Reports mit automatischer sicherer URL-Generierung
    # GET /api/reports/{report_id}/with-secure-url/
    # Lädt Report-Daten inklusive Metadaten und generiert automatisch eine sichere URL
    path('reports/<int:report_id>/with-secure-url/', 
         ReportWithSecureUrlView.as_view(), 
         name='report_with_secure_url'),

    # API-Endpunkt für manuelle sichere URL-Generierung  
    # POST /api/secure-file-urls/
    # Body: {"report_id": 123, "file_type": "pdf"}
    # Generiert eine neue sichere URL für einen bestehenden Report
    path('secure-file-urls/', 
         SecureFileUrlView.as_view(), 
         name='generate_secure_file_url'),

    # API-Endpunkt für Report-Datei-Metadaten
    # GET /api/reports/{report_id}/file-metadata/
    # Gibt Datei-Metadaten zurück (Größe, Typ, Datum, etc.)
    path('reports/<int:report_id>/file-metadata/', 
         ReportFileMetadataView.as_view(), 
         name='report_file_metadata'),

    # Sichere Datei-Serving-Endpunkt mit Token-Validierung
    # GET /api/reports/{report_id}/secure-file/?token={token}
    # Serviert die tatsächliche Datei über eine sichere, tokenbasierte URL
    path('reports/<int:report_id>/secure-file/', 
         SecureFileServingView.as_view(), 
         name='secure_file_serving'),

    # URL-Validierungs-Endpunkt
    # GET /api/validate-secure-url/?url={url}
    # Validiert, ob eine sichere URL noch gültig ist
    path('validate-secure-url/', 
         validate_secure_url, 
         name='validate_secure_url'),

    # ---------------------------------------------------------------------------------------

    # PatientFinding related endpoints
    path(
        'patient-finding-locations/',
        create_patient_finding_location,
        name='create_patient_finding_location'
    ),
    path(
        'patient-finding-morphologies/',
        create_patient_finding_morphology,
        name='create_patient_finding_morphology'
    ),

    # ---------------------------------------------------------------------------------------

    #this is for, to test the timeline
    #need to delete this url and also endoreg_db_production/endoreg_db/views/views_for_timeline.py and endoreg_db_production/endoreg_db/templates/timeline.html
    path('video/<int:video_id>/timeline/', video_timeline_view, name='video_timeline'),

    # ---------------------------------------------------------------------------------------
    # STATISTICS API ENDPOINTS
    #
    # Diese Endpunkte stellen Dashboard-Statistiken bereit für das Frontend
    # ---------------------------------------------------------------------------------------
    
    # Examination Statistics API
    # GET /api/examinations/stats/
    # Liefert Statistiken über Examinations und PatientExaminations
    path('examinations/stats/', ExaminationStatsView.as_view(), name='examination_stats'),
    
    # Video Segment Statistics API  
    # GET /api/video-segment/stats/  (Note: singular 'segment' to match frontend)
    # Liefert Statistiken über Video-Segmente und Label-Verteilung
    path('video-segment/stats/', VideoSegmentStatsView.as_view(), name='video_segment_stats'),
    
    # Alternative Video Segments Statistics API (plural version for compatibility)
    # GET /api/video-segments/stats/
    path('video-segments/stats/', VideoSegmentStatsView.as_view(), name='video_segments_stats'),
    
    # Sensitive Meta Statistics API
    # GET /api/video/sensitivemeta/stats/
    # Liefert Statistiken über SensitiveMeta-Einträge und Verifikationsstatus
    path('video/sensitivemeta/stats/', SensitiveMetaStatsView.as_view(), name='sensitive_meta_stats'),
    
    # General Dashboard Statistics API
    # GET /api/stats/
    # Liefert allgemeine Übersichtsstatistiken für das Dashboard
    path('stats/', GeneralStatsView.as_view(), name='general_stats'),

    # ---------------------------------------------------------------------------------------
    # SENSITIVE META API ENDPOINTS & FILE SELECTION
    #
    # New API endpoints for sensitive meta data management and file selection
    # These endpoints support both PDF and video anonymization workflows
    # ---------------------------------------------------------------------------------------
    
    # Available Files List API
    # GET /api/available-files/?type=pdf|video|all&limit=50&offset=0
    # Lists available PDF and video files for anonymization selection
    path('available-files/', 
         AvailableFilesListView.as_view(), 
         name='available_files_list'),
    
    # Sensitive Meta Verification API
    # POST /api/pdf/sensitivemeta/verify/
    # For updating verification flags specifically
    path('pdf/sensitivemeta/verify/', 
         SensitiveMetaVerificationView.as_view(), 
         name='sensitive_meta_verify'),
    
    # Sensitive Meta List API
    # GET /api/pdf/sensitivemeta/list/
    # For listing sensitive meta entries with filtering
    path('pdf/sensitivemeta/list/', 
         SensitiveMetaListView.as_view(), 
         name='sensitive_meta_list'),

    # Video Sensitive Meta endpoints (for video anonymization)
    # GET /api/video/sensitivemeta/<int:sensitive_meta_id>/
    # PATCH /api/video/sensitivemeta/<int:sensitive_meta_id>/
    path('video/sensitivemeta/<int:sensitive_meta_id>/', 
         SensitiveMetaDetailView.as_view(), 
         name='video_sensitive_meta_detail'),

]

# Always serve media files, not just in DEBUG
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
# Optionally, always serve static files as well
urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)