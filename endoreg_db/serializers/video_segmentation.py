from pathlib import Path
from rest_framework import serializers
from django.http import FileResponse, Http404,StreamingHttpResponse
from ..models import RawVideoFile
import subprocess
from django.conf import settings


class VideoFileSerializer(serializers.ModelSerializer):

    
    """
    Serializer that dynamically handles video retrieval and streaming.
    Ensures file returns the relative file path (not MEDIA_URL)
    Computes full_video_path using the correct storage path (/home/admin/test-data)-need to change make it dynamic
    Returns video_url for frontend integration
    Serves the video file when needed
    
    """

    video_url = serializers.SerializerMethodField()
    full_video_path = serializers.SerializerMethodField()
    file = serializers.SerializerMethodField()  # Override file to remove incorrect MEDIA_URL behavior,otherwise:Django's FileField automatically generates a URL based on MEDIA_URL
    # Video dropdown field for frontend selection (currently shows video ID, but can be changed later)
    video_selection_field = serializers.SerializerMethodField()
    #classification_data = serializers.SerializerMethodField() #data from database (smooth prediction values but currently hardcoded one)
    #The Meta class tells Django what data to include when serializing a RawVideoFile object.
    sequences = serializers.SerializerMethodField()
    label_names = serializers.SerializerMethodField()
     # Convert selected label frames into time segments (seconds)
    label_time_segments = serializers.SerializerMethodField()
    label_predictions = serializers.SerializerMethodField()



    class Meta:
        model = RawVideoFile
        #he fields list defines which data should be included in the API response.
        fields = ['id', 'file', 'video_url', 'full_video_path','video_selection_field','label_names','sequences','label_time_segments',]  #  Ensure computed fields are included

    def get_video_selection_field(self,obj):
        """
        Returns the field used for video selection in the frontend dropdown.
        Currently, it shows the video ID, but this can be changed easily later.
        """
        return obj.id
      
    def get_video_url(self, obj): # when we serialize a RawVideoFile object (video metadata), the get_video_url method is automatically invoked by DRF
        """
        Returns the API endpoint where the frontend can fetch the video.
        """
        if not obj.id:
            return {"error": "Invalid video ID"}
        
        request= self.context.get('request') #Gets the request object (provided by DRF).
        if request:
            return request.build_absolute_uri(f"/api/video/{obj.id}/")
        
        return {"error": "Video URL not avalaible"}

    def get_file(self, obj):
        """
        Ensures the file field returns only the relative path, adn also validates it
        """
        if not obj.file:
            return {"error": "No file  associated with this entry"}
        #obj.file.name is an attribute of FieldFile that returns the file path as a string and name is not the database attribute, it is an attribute of Django’s FieldFile object that holds the file path as a string.
        if not hasattr(obj.file,'name') or not obj.file.name.strip():
            return {"error": "Invalid file name"}

        return str(obj.file.name).strip()  #  Only return the file path, no URL,#obj.file returning a FieldFile object instead of a string


    '''The error "muxer does not support non-seekable output" 
    happens because MP4 format requires seeking, but FFmpeg does not support writing MP4 directly to a non-seekable stream (like STDOUT).'''

    def get_full_video_path(self, obj):
        """
        Constructs the absolute file path dynamically.
        - Uses the actual storage directory (`/home/admin/test-data/`)
        """
        if not obj.file:
            return {"error": "No video file associated with this entry"}
        
        video_relative_path = str(obj.file.name).strip()  #  Convert FieldFile to string
        if not video_relative_path:
            return {"error":"Video file path is empty or invalid"}  #  none might cause, 500 error, Handle edge case where the file name is empty
        
        print("-----------------------------------------")
       # pseudo_dir = settings.PSEUDO_DIR
        #print(f"Using pseudo directory: {pseudo_dir}")

        #   full path using the actual storage directory
        actual_storage_dir = Path("/home/admin/test-data")  # need to change
        #actual_storage_dir = pseudo_dir
        full_path = actual_storage_dir / video_relative_path
        #full_path = Path("/home/admin/test-data/video/lux-gastro-video.mp4")


        return str(full_path) if full_path.exists() else {"error":f"file not found at: {full_path}"}

        '''
        ffmpeg_command = [
        "ffmpeg", "-i", str(full_path),  # Input video
        "-vf", "drawtext=text='OUTSIDE':fontcolor=white:fontsize=24:x=(w-text_w)/2:y=30:enable='lt(t,10)'",
        "-c:v", "libx264", "-preset", "ultrafast",  # Encode quickly for streaming
        "-f", "mp4", "-"  #  Output to STDOUT (no file saving)
    ]'''
        '''ffmpeg_command = [
        "ffmpeg", "-i", str(full_path),  # Input video
        "-vf", "drawtext=text='OUTSIDE':fontcolor=white:fontsize=24:x=(w-text_w)/2:y=30:enable='lt(t,10)'",
        "-c:v", "libx264", "-preset", "ultrafast",
        "-f", "mpegts", "pipe:1"  #  Output as MPEG-TS to STDOUT
    ]'''
        
    def get_sequences(self, obj):
        """
        Extracts the sequences field from the RawVideoFile model.
        Example Output:
        {
            "outside": [[1, 32], [123, 200]],
            "needle": [[36, 141]],
            "kolonpolyp": [[91, 126]]
        }
        """
        return obj.sequences or {"error":"no sequence found, check database first"}  #  Get from sequences, return {} if missing 
       
    def get_label_names(self, obj):
        """
        Extracts only label names from the sequences data.
        Example Output:
        ["outside", "needle", "kolonpolyp"]
        """
        sequences = self.get_sequences(obj)
        return list(sequences.keys()) if sequences else []
    

    def get_label_time_segments(self, obj):
        """
        Converts frame sequences of a selected label into time segments in seconds.
        Also retrieves frame-wise predictions for the given label.
        """

        FPS = 50  # Frames per second, should be dynamic if stored in DB

        sequences = self.get_sequences(obj)  # Get all sequences from the database
        readable_predictions = obj.readable_predictions  # Frame-wise predictions from DB, need to change the field name for smooth predicitons values

        if not isinstance(readable_predictions, list):
            return {"error": "Invalid prediction data format. Expected a list."}

        time_segments = {}  # Dictionary to store converted times and frame predictions

        for label, frame_ranges in sequences.items():
            label_times = []  # Stores time segments
            label_predictions = {}  # Fix: Initialize label_predictions here

            for frame_range in frame_ranges:
                if len(frame_range) != 2:
                    continue  # Skip invalid frame ranges

                start_frame, end_frame = frame_range

                # Convert frames to time in seconds
                start_time = start_frame / FPS
                end_time = end_frame / FPS

                # Fetch predictions for frames within this range
                frame_predictions = {}
                for frame_num in range(start_frame, end_frame + 1):
                    if 0 <= frame_num < len(readable_predictions):  # Ensure index is valid
                        frame_predictions[frame_num] = readable_predictions[frame_num]

                # Append the converted time segment
                label_times.append({"start_time": round(start_time, 2), "end_time": round(end_time, 2)})
                label_predictions.update(frame_predictions)  # Fix: Store predictions correctly

            # Store both time segments and frame predictions under the label
            time_segments[label] = {
                "time_ranges": label_times,
                "frame_predictions": label_predictions,  # Fix: Ensure label_predictions is correctly assigned per label
            }

        return time_segments



    

    # Use StreamingHttpResponse to stream FFmpeg output to browser
        #return StreamingHttpResponse(subprocess.Popen(ffmpeg_command, stdout=subprocess.PIPE).stdout, content_type="video/mp4")

        # Ensure the path exists before returning
       # return str(full_path) if full_path.exists() else None

'''    def get_classification_data(self, obj):
        """
        Returns binary classification data in a **scalable** way.
        Currently hardcoded for testing, but can later integrate a model.
        """
        classifications = [
            {
                "label": "OUTSIDE",
                "start_time": 0.1,
                "end_time": 19,
                "confidence": 0.85,  # Hardcoded but has to - dynamically computed
            },
            
            {
                "label": "Needle",
                "start_time": 36,
                "end_time": 141,
                "confidence": 0.95,  # Hardcoded but has to - dynamically computed
            },
            {
                "label": "Kolonpolyp",
                "start_time": 91,
                "end_time": 126,
                "confidence": 0.89,  # Hardcoded but has to - dynamically computed
            }
        ]

        return classifications'''






"""
await import('https://cdn.jsdelivr.net/npm/axios/dist/axios.min.js');
const videoId = 1;  
axios.get(`http://localhost:8000/api/video/${videoId}/`, {
    headers: {
        'Accept': 'application/json' }
})
.then(response => {
    console.log("Video Metadata:", response.data);

    const videoUrl = response.data.video_url;

    const videoElement = document.createElement("video");
    videoElement.src = videoUrl;
    videoElement.controls = true;
    videoElement.width = 600;  
    document.body.appendChild(videoElement);
})
.catch(error => {
    console.error("Error Fetching Video:", error.response ? error.response.data : error);
});

"""''