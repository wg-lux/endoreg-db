from .views.csrf import csrf_token_view
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views.patient_views import (
    PatientViewSet,
    start_examination,
    get_location_choices,
    get_morphology_choices, 
)
from .views.Frames_NICE_and_PARIS_classifications_views import ForNiceClassificationView, ForParisClassificationView
# endoreg_db_production/endoreg_db/urls.py
from .views.keycloak_views import KeycloakVideoView, keycloak_login, keycloak_callback, public_home
#from .views.feature_selection_view import FetchSingleFramePredictionView // its implemented in endo-ai other project need to add here
from .views.video_segmentation_views import VideoView, VideoLabelView,UpdateLabelSegmentsView
from .views.views_for_timeline import video_timeline_view
from .views.raw_video_meta_validation_views import VideoFileForMetaView, VideoFileForMetaView
from .views.raw_pdf_meta_validation_views import PDFFileForMetaView
from .views.raw_pdf_meta_validation_views import UpdateSensitiveMetaView
from .views.raw_pdf_anony_text_validation_views import RawPdfAnonyTextView, UpdateAnonymizedTextView
from .views.examination_views import (
    ExaminationViewSet,
    get_morphology_classification_choices_for_exam,
    get_location_classification_choices_for_exam,
    get_interventions_for_exam,
    get_instruments_for_exam,
)


router = DefaultRouter()
router.register(r'patients', PatientViewSet)


urlpatterns = [
    path('', include(router.urls)),
    path('start-examination/', start_examination, name="start_examination"),
    path('get-location-choices/<int:location_id>/', get_location_choices, name="get_location_choices"),
    path('get-morphology-choices/<int:morphology_id>/', get_morphology_choices, name="get_morphology_choices"),
    path('examinations/', ExaminationViewSet.as_view({'get': 'list'}), name='examination-list'),
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

#--------------------------------------START : VIDEO SEGMENTATION END POINTS--------------------------------------

       # The dropdown contains video names and their corresponding IDs, which are retrieved from the database(RawVideoFile). Additionally, this route(videos) also fetches labels along with their names and IDs from the label table.
       # We will modify this implementation later as per our requirements.
        #Based on the selected video ID, relevant data will be fetched from the database, and the video, along with its associated details, 
        # will be displayed on the frontend using route(video/<int:video_id>/).
        # once label is selected from the dropdown,using its name, details can be fetched from rawvideofile using route("video/<int:video_id>/label/<str:label_name>/) 
        #If editing is required, a form will be available for each label. This form dynamically updates when the selected label changes. It will display all segments associated with the selected label, each with a delete option. Below these segments, there may be a button for adding more segments.
        #If any values in the form are modified, the updated data will be saved in the dafrom .views import VideoView, keycloak_login, keycloak_callbacktabase table.
    
    
    
    
    # Fetch all videos for the dropdown
    # Fetch all available videos (for the video selection dropdown in frontend)
    # Usage in frontend: Call this API to get a list of available videos.
    # Example request: GET /videos/
    # Response: Returns a JSON list of videos (id, file path, video_url, sequences, label_names).
    # it also fetch the label from teh label table

    # for keycloak:-
    # /videos/ , protected API
    # /login/ , sends user to Keycloak login page
    # /login/callback/ , where Keycloak sends the user after login
    
    path('endoreg_db/', public_home, name='public_home'),
    path('videos/', VideoView.as_view(), name='video_list'),
    path('login/', keycloak_login, name='keycloak_login'),
    path('login/callback/', keycloak_callback, name='keycloak_callback'),
    
    #need to change this route
    #path('prediction/', FetchSingleFramePredictionView.as_view(), name='fetch-single-frame-prediction'),#.as_view() converts the class-based view into a function that Django can use for routing.
    
    # Fetch video details + labels based on selected video
    # Usage in frontend: Call this when a user selects a video from the dropdown.
    # Example request: GET /video/1/
    # Response: Returns metadata, file path, video URL, sequences, and available label names.
    # Frontend should use this api for `label_names`, to display label selection (dropdown or tabs).
    path("video/<int:video_id>/", VideoView.as_view(), name="video_handler"),
    

    # Fetch time segments along with frame predictions and filenames for a selected label
    # 
    # **Usage in frontend:**
    # - Call this API when a user selects a label from the dropdown in the timeline.
    # - The API returns time segments (start & end times in seconds) where the label appears in the video.
    # - Each segment includes frame-wise predictions and the corresponding frame filenames.
    #
    # **Example request:**
    #   GET /video/1/label/outside/
    #
    # **Example response:**
    # {
    #     "label": "outside",
    #     "time_segments": [
    #         {   "segment_start": 0,
     #       "segment_end": 463,
    #             "start_time": 0.0,
    #             "end_time": 9.26,
    #             "frames": {
    #                 "0": {
    #                     "frame_filename": "frame_0000000.jpg",
    #                     "frame_file_path": "/home/admin/test-data/video_frames/abc123/frame_0000000.jpg",
    #                     "predictions": {
    #                         "appendix": 0.0150,
    #                         and others
    #                     }
    #                 },
    #                 "1": {
    #                     "frame_filename": "frame_0000001.jpg",
    #                     "frame_file_path": "/home/admin/test-data/video_frames/abc123/frame_0000001.jpg",
    #                     "predictions": {
    #                         "appendix": 0.0124,
    #                         and otehrs
    #                     }}}}]}
    #                 
    # start_time and end_time are in seconds, converted from frame indices.
    # frames contains all frames in the segment with:
    # frame_filename (e.g., frame_0000001.jpg)
    # frame_file_path (full path for easy access)
    #  predictions (frame-wise probability scores for each category)
    #we need label name for this not id as we donot have id in rawvideofile table
    path("video/<int:video_id>/label/<str:label_name>/", VideoLabelView.as_view(), name="video_label_times"),

    # Update or create label segments for a specific video and label
    # - Call this API when a user updates the start and end frames of a label segment(adding new , deleting or changing the values).
    # - The API will:
    #   - **Update** existing segments if they match the given `video_id` and `label_id`.
    #   - **Create** new segments if they do not already exist.
    #   - **Delete** segments from the database if they were removed from the frontend.
    # - This ensures that the database reflects exactly what is sent from the frontend.
    #
    # **Example request (Updating segments for label ID 12 in video ID 1):**
    #   PUT /video/1/label/12/update_segments/
    #
    # **Request body (JSON):**
    # {
    #     "video_id": 1,
    #     "label_id": 12,
    #     "segments": [
    #         {
    #             "start_frame_number": 0,
    #             "end_frame_number": 463
    #         },{
    #             "start_frame_number": 500,
    #             "end_frame_number": 700
    #         }]}
    #
    # **Expected response (If successful):**
    # {
    #     "updated_segments": [
    #         {
    #             "id": 1,
    #             "video_id": 1,
    #             "label_id": 12,
    #             "start_frame_number": 0,
    #             "end_frame_number": 463
    #         }
    #     ],
    #     "new_segments": [
    #         {
    #             "id": 2,
    #             "video_id": 1,
    #             "label_id": 12,
    #             "start_frame_number": 500,
    #             "end_frame_number": 700
    #         }
    #     ],
    #     "deleted_segments": 1  # Number of deleted segments if any were removed
    # }
 
    # **Frontend Integration:**
    # - Vue.js should send this request when the user **saves changes** to segments.
    # - The **"Add Segment"** button should allow users to add new segments dynamically.
    # - The **"Delete Segment"** button should remove segments from the list before submitting.
    # - The request should be triggered via an `axios.put` call:
    #
    path("video/<int:video_id>/label/<int:label_id>/update_segments/", UpdateLabelSegmentsView.as_view(), name="update_label_segments"),


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
        
    #The first request (without id) fetches the first available PDF metadata.
    #The "Next" button (with id) fetches the next available PDF.
    #If an id is provided, the API returns the actual PDF file instead of JSON.
    path("pdf/sensitivemeta/", PDFFileForMetaView.as_view(), name="pdf_meta"),  




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
    # NICE CLASSIFICATION FRAME SELECTION ENDPOINT
    #
    # API to return **3 diverse polyp + chromo segments** per video and **5 low_quality-filtered frames** per segment.
    #
    #  What it does:
    # - Automatically loops over all videos in the database.
    # - For each video:
    #   - Finds segments where both `"polyp"` and `"digital_chromo_endoscopy"` labels overlap.
    #   - From matching sequences:
    #     - Selects 3 **diverse** segments that are:
    #         - At least 2 seconds (100 frames) long
    #         - At least 10 seconds (500 frames) apart
    #     - If > 3 valid segments are found:
    #         - Chooses the 3 that are **most spread out in time** (max total gap)
    #     - If < 3 valid sequences:
    #         - Falls back to top 3 longest available sequences
    #   - For each selected segment:
    #     - Selects 5 frames that:
    #         - Have the **lowest "low_quality" prediction**
    #         - Are at least 2 seconds (100 frames) apart from each other
    #
    #  Fully configurable via constants at the top:
    #   - POLYP_LABEL_NAME, CHROMO_LABEL_NAME
    #   - FPS, MIN_SEGMENT_LENGTH_SECONDS, MIN_SEQUENCE_GAP_SECONDS, etc.
    #
    # Example URL:
    #   GET /videos/nice-classification/
    #
    #  Example response (per segment):
    # [
    #     {
    #         "video_id": 5,
    #         "segment_start": 125,
    #         "segment_end": 275,
    #         "frames": [
    #             {
    #                 "frame_number": 130,
    #                 "low_quality": 0.021,
    #                 "frame_path": "/home/.../frame_0000130.jpg"
    #             },
    #             ...
    #         ]
    #     },
    #     ...
    # ]
    #
    #  Frontend Usage:
    # - Trigger this from Vue.js when clinician or AI needs to preview high-quality polyp classification frames.
    # - Ideal for NICE classification training dataset generation or QA workflows.
    path('video/niceclassification/', ForNiceClassificationView.as_view(), name="niceclassification"),
    path('video/parisclassification/', ForParisClassificationView.as_view(), name="parisclassification"),

    # ---------------------------------------------------------------------------------------


















    #this is for, to test the timeline
    #need to delete this url and also endoreg_db_production/endoreg_db/views/views_for_timeline.py and endoreg_db_production/endoreg_db/templates/timeline.html
    path('video/<int:video_id>/timeline/', video_timeline_view, name='video_timeline'),
    ]

    
#https://biigle.de/manual/tutorials/videos/navigating-timeline#for time line example
from django.conf import settings
from django.conf.urls.static import static
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)