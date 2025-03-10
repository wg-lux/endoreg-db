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
from .views.video_segmentation_views import VideoView, VideoLabelView

router = DefaultRouter()
router.register(r'patients', PatientViewSet)

urlpatterns = [
    path('start-examination/', start_examination, name="start_examination"),
    path('get-location-choices/<int:location_id>/', get_location_choices, name="get_location_choices"),
    path('get-morphology-choices/<int:morphology_id>/', get_morphology_choices, name="get_morphology_choices"),
    path('api/', include(router.urls)),
    path('api/conf/', csrf_token_view, name='csrf_token'),
    
    
    # Fetch all videos for the dropdown
    # Fetch all available videos (for the video selection dropdown in frontend)
    # Usage in frontend: Call this API to get a list of available videos.
    # Example request: GET /api/videos/
    # Response: Returns a JSON list of videos (id, file path, video_url, sequences, label_names).
    path("api/videos/", VideoView.as_view(), name="video_list"),
    
    #need to change this route
    #path('api/prediction/', FetchSingleFramePredictionView.as_view(), name='fetch-single-frame-prediction'),#.as_view() converts the class-based view into a function that Django can use for routing.
    
    # Fetch video details + labels based on selected video
    # Usage in frontend: Call this when a user selects a video from the dropdown.
    # Example request: GET /api/video/1/
    # Response: Returns metadata, file path, video URL, sequences, and available label names.
    # Frontend should use this api for `label_names`, to display label selection (dropdown or tabs).
    #path("api/video/<int:video_id>/", VideoView.as_view(), name="video_handler"),
    path("api/video/<int:video_id>/", VideoView.as_view(), name="video_handler"),
    

    # Fetch time segments (start and end times in seconds) for a selected label
    # Usage in frontend: Call this API when a user selects a label from the dropdown.
    # Example request: GET /api/video/1/label/outside/
    # Response: Returns the label name and a list of time segments. {"label": "polyp","time_segments": [{"start_time": 221.4,"end_time": 240.88},{"start_time": 241.18,"end_time": 241.18}]}
    # Each time segment contains the start and end time (converted from frames), currently skiping the segment which has no start and end, also getting the prediciton from read_able prediciton of each frame(need to change iwth smooth prediciton values)
    # Frontend should use this API to display the timeline for the selected label.
    path("api/video/<int:video_id>/label/<str:label_name>/", VideoLabelView.as_view(), name="video_label_times"),

    
    ]



#https://biigle.de/manual/tutorials/videos/navigating-timeline#for time line example
