from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views.patient_views import (
    PatientViewSet,
    start_examination,
    get_location_choices,
    get_morphology_choices, 
)
from .views.csrf import csrf_token_view
#from .views.feature_selection_view import FetchSingleFramePredictionView // its implemented in ando-ai other project need to add here
from .views.video_segmentation_views import VideoView, VideoLabelView,UpdateLabelSegmentsView
from .views.views_for_timeline import video_timeline_view
from .views.raw_video_meta_validation_views import VideoFileForMetaView
router = DefaultRouter()
router.register(r'patients', PatientViewSet)

urlpatterns = [
    path('start-examination/', start_examination, name="start_examination"),
    path('get-location-choices/<int:location_id>/', get_location_choices, name="get_location_choices"),
    path('get-morphology-choices/<int:morphology_id>/', get_morphology_choices, name="get_morphology_choices"),
    path('api/', include(router.urls)),
    path('api/conf/', csrf_token_view, name='csrf_token'),
    


#--------------------------------------VIDEO SEGMENTATION END POINTS--------------------------------------

       # The dropdown contains video names and their corresponding IDs, which are retrieved from the database(RawVideoFile). Additionally, this route(api/videos) also fetches labels along with their names and IDs from the label table.
       # We will modify this implementation later as per our requirements.
        #Based on the selected video ID, relevant data will be fetched from the database, and the video, along with its associated details, 
        # will be displayed on the frontend using route(api/video/<int:video_id>/).
        # once label is selected from the dropdown,using its name, details can be fetched from rawvideofile using route("api/video/<int:video_id>/label/<str:label_name>/) 
        #If editing is required, a form will be available for each label. This form dynamically updates when the selected label changes. It will display all segments associated with the selected label, each with a delete option. Below these segments, there may be a button for adding more segments.
        #If any values in the form are modified, the updated data will be saved in the database table.
    
    
    
    
    # Fetch all videos for the dropdown
    # Fetch all available videos (for the video selection dropdown in frontend)
    # Usage in frontend: Call this API to get a list of available videos.
    # Example request: GET /api/videos/
    # Response: Returns a JSON list of videos (id, file path, video_url, sequences, label_names).
    # it also fetch the label from teh label table
    path("api/videos/", VideoView.as_view(), name="video_list"),
    
    #need to change this route
    #path('api/prediction/', FetchSingleFramePredictionView.as_view(), name='fetch-single-frame-prediction'),#.as_view() converts the class-based view into a function that Django can use for routing.
    
    # Fetch video details + labels based on selected video
    # Usage in frontend: Call this when a user selects a video from the dropdown.
    # Example request: GET /api/video/1/
    # Response: Returns metadata, file path, video URL, sequences, and available label names.
    # Frontend should use this api for `label_names`, to display label selection (dropdown or tabs).
    path("api/video/<int:video_id>/", VideoView.as_view(), name="video_handler"),
    

    # Fetch time segments along with frame predictions and filenames for a selected label
    # 
    # **Usage in frontend:**
    # - Call this API when a user selects a label from the dropdown in the timeline.
    # - The API returns time segments (start & end times in seconds) where the label appears in the video.
    # - Each segment includes frame-wise predictions and the corresponding frame filenames.
    #
    # **Example request:**
    #   GET /api/video/1/label/outside/
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
    path("api/video/<int:video_id>/label/<str:label_name>/", VideoLabelView.as_view(), name="video_label_times"),

    # Update or create label segments for a specific video and label
    # - Call this API when a user updates the start and end frames of a label segment(adding new , deleting or changing the values).
    # - The API will:
    #   - **Update** existing segments if they match the given `video_id` and `label_id`.
    #   - **Create** new segments if they do not already exist.
    #   - **Delete** segments from the database if they were removed from the frontend.
    # - This ensures that the database reflects exactly what is sent from the frontend.
    #
    # **Example request (Updating segments for label ID 12 in video ID 1):**
    #   PUT /api/video/1/label/12/update_segments/
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
    path("api/video/<int:video_id>/label/<int:label_id>/update_segments/", UpdateLabelSegmentsView.as_view(), name="update_label_segments"),

#----------------------------------END--VIDEO SEGMENTATION SECTION-------------------------------
    #this is for to test the timeline
    #need to delete this url and also endoreg_db_production/endoreg_db/views/views_for_timeline.py and endoreg_db_production/endoreg_db/templates/timeline.html
    path('video/<int:video_id>/timeline/', video_timeline_view, name='video_timeline'),

    
    
    
    #  const url = lastId ? `http://localhost:8000/api/video/meta/?last_id=${lastId}` : "http://localhost:8000/api/video/meta/";
    path("api/video/sensitivemeta/", VideoFileForMetaView.as_view(), name="video_meta"),  # Single endpoint for both first and next video    
    ]






#https://biigle.de/manual/tutorials/videos/navigating-timeline#for time line example
