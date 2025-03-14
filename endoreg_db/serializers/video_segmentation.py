from pathlib import Path
from rest_framework import serializers
from django.http import FileResponse, Http404,StreamingHttpResponse
from ..models import RawVideoFile,Label
import subprocess, cv2
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
    #label_predictions = serializers.SerializerMethodField()
    original_file_name = serializers.CharField()
    duration = serializers.SerializerMethodField()

    



    class Meta:
        model = RawVideoFile
        #he fields list defines which data should be included in the API response.
        fields = ['id','original_file_name', 'file','duration', 'video_url', 'full_video_path','video_selection_field','label_names','sequences','label_time_segments']  #  Ensure computed fields are included
    #@staticmethod #using @staticmethod makes it reusable without needing to create a serializer instance.
    #  Without @staticmethod, you would need to instantiate the serializer before calling the method, which is unnecessary her
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
    
    def get_duration(self, obj):
        """
        Returns the total duration of the video in seconds.
        If duration is not stored in the database, it extracts it dynamically using OpenCV.
        """
        if hasattr(obj, "duration") and obj.duration:
            return obj.duration  # If duration is stored in the database, return it directly.

        # Dynamically extract duration if not stored
        video_path = obj.file.path
        cap = cv2.VideoCapture(video_path)

        if not cap.isOpened():
            return None  # Error handling if video can't be opened

        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        cap.release()

        return round(total_frames / fps, 2) if fps > 0 else None  # Return duration in seconds


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

        #   full path using the actual storage directory~
        #actual_storage_dir = Path("~/test-data")  # need to change
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

        Includes:
        - Frame index
        - Corresponding frame filename (frame_0000001.jpg)
        - Full frame file path for frontend access
        - segment_start and segment_end (in frame index format, not divided by FPS)
        """

        fps = obj.fps if hasattr(obj, "fps") and obj.fps is not None else obj.get_fps() if hasattr(obj, "get_fps") and obj.get_fps() is not None else 50

        print("here is fps::::::::::::::::::.-----------::::::",fps)
        sequences = self.get_sequences(obj)  # Fetch sequence data
        readable_predictions = obj.readable_predictions  # Predictions from DB

        if not isinstance(readable_predictions, list):
            return {"error": "Invalid prediction data format. Expected a list."}

        frame_dir = Path(obj.frame_dir)  # Get the correct directory from the model

        time_segments = {}  # Dictionary to store converted times and frame predictions

        for label, frame_ranges in sequences.items():
            label_times = []  # Stores time segments
            frame_predictions = {}  # Ensure frame_predictions is properly initialized for each label

            for frame_range in frame_ranges:
                if len(frame_range) != 2:
                    continue  # Skip invalid frame ranges

                start_frame, end_frame = frame_range  # Raw frame indices from DB
                start_time = start_frame / fps  # Convert frame index to seconds
                end_time = end_frame / fps  # Convert frame index to seconds

                frame_data = {}  # Store frame-wise info

                # Fetch predictions for frames within this range
                for frame_num in range(start_frame, end_frame + 1):
                    if 0 <= frame_num < len(readable_predictions):  # Ensure index is valid
                        frame_filename = f"frame_{str(frame_num).zfill(7)}.jpg"  # Frame filename format
                        frame_path = frame_dir / frame_filename  # Full path to the frame

                        frame_data[frame_num] = {
                            "frame_filename": frame_filename,
                            "frame_file_path": str(frame_path),
                            "predictions": readable_predictions[frame_num]
                        }

                        # Store frame-wise predictions in frame_predictions
                        frame_predictions[frame_num] = readable_predictions[frame_num]

                # Append the converted time segment
                label_times.append({
                    "segment_start": start_frame,  # Raw start frame (not divided by FPS)
                    "segment_end": end_frame,  # Raw end frame (not divided by FPS)
                    "start_time": round(start_time, 2),  # Converted start time in seconds
                    "end_time": round(end_time, 2),  # Converted end time in seconds
                    "frames": frame_data  # Attach frame details
                })

            # Store time segments and frame_predictions under the label
            time_segments[label] = {
                "time_ranges": label_times,
                "frame_predictions": frame_predictions  # Ensure frame_predictions is correctly assigned
            }

        return time_segments

class VideoListSerializer(serializers.ModelSerializer):
    """
    Minimal serializer to return only `id` and `original_file_name`
    for the video selection dropdown in Vue.js.
    """
    class Meta:
        model = RawVideoFile
        fields = ['id', 'original_file_name']  # Only fetch required fields



from pathlib import Path
from rest_framework import serializers
from django.http import FileResponse, Http404, StreamingHttpResponse
from ..models import RawVideoFile, Label, LabelRawVideoSegment  # Importing necessary models
import subprocess
from django.conf import settings
from django.db.models import Q  # Import Q for better querying


class LabelSerializer(serializers.ModelSerializer):
    """
    Serializer for fetching labels from the `endoreg_db_label` table.
    Includes `id` (for backend processing) and `name` (for dropdown display in Vue.js).
    """
    class Meta:
        model = Label
        fields = ['id', 'name']


class LabelSegmentSerializer(serializers.ModelSerializer):
    """
    Serializer for retrieving label segments from `endoreg_db_labelrawvideosegment`.
    """
    class Meta:
        model = LabelRawVideoSegment
        fields = ['id', 'video_id', 'label_id', 'start_frame_number', 'end_frame_number']


from django.db import transaction

from django.db import transaction

class LabelSegmentUpdateSerializer(serializers.Serializer):
    """
    Serializer for updating label segments.

    - Ensures that the segments stored in the database match exactly with what is sent from the frontend.
    - Updates existing segments if their `start_frame_number` matches but `end_frame_number` has changed.
    - Inserts new segments if they are not already present in the database.
    - Deletes extra segments from the database if they are no longer in the frontend data.
    """

    video_id = serializers.IntegerField()
    label_id = serializers.IntegerField()
    segments = serializers.ListField(
        child=serializers.DictField(
            child=serializers.FloatField()  # Ensure we handle float values
        )
    )

    def validate(self, data):
        """
        Validates that all required segment fields are provided correctly.

        - Ensures that each segment contains `start_frame_number` and `end_frame_number`.
        - Validates that `start_frame_number` is always less than or equal to `end_frame_number`.
        """
        if not data.get("segments"):
            raise serializers.ValidationError("No segments provided.")

        for segment in data["segments"]:
            if "start_frame_number" not in segment or "end_frame_number" not in segment:
                raise serializers.ValidationError("Each segment must have `start_frame_number` and `end_frame_number`.")

            if segment["start_frame_number"] > segment["end_frame_number"]:
                raise serializers.ValidationError("Start frame must be less than or equal to end frame.")

        return data

    def save(self):
        """
        Updates, inserts, and deletes label segments to ensure database consistency.

        Steps:
        1. Fetch all existing segments for the given `video_id` and `label_id`.
        2. Compare existing segments with the new ones from the frontend.
        3. Update segments where `start_frame_number` exists but `end_frame_number` has changed.
        4. Insert new segments that are not already in the database.
        5. Delete segments that exist in the database but are missing from the frontend data.
        """

        video_id = self.validated_data["video_id"]
        label_id = self.validated_data["label_id"]
        new_segments = self.validated_data["segments"]

        # Fetch all existing segments for this video and label from the database
        existing_segments = LabelRawVideoSegment.objects.filter(video_id=video_id, label_id=label_id)

        # Convert existing segments into a dictionary for quick lookup
        # Key format: (start_frame_number, end_frame_number)
        existing_segments_dict = {(float(seg.start_frame_number), float(seg.end_frame_number)): seg for seg in existing_segments}

        # Prepare lists for batch processing
        updated_segments = []  # Stores segments that need to be updated
        new_entries = []  # Stores segments that need to be created
        existing_keys = set(existing_segments_dict.keys())  # Existing database segment keys
        new_keys = set((float(seg["start_frame_number"]), float(seg["end_frame_number"])) for seg in new_segments)  # New frontend segment keys

        # Start a transaction to ensure database consistency
        with transaction.atomic():
            for segment in new_segments:
                start_frame = float(segment["start_frame_number"])
                end_frame = float(segment["end_frame_number"])

                if (start_frame, end_frame) in existing_keys:
                    # If segment with exact start_frame and end_frame already exists, no change is needed
                    continue
                else:
                    # Check if a segment exists with the same start_frame but different end_frame
                    existing_segment = LabelRawVideoSegment.objects.filter(
                        video_id=video_id,
                        label_id=label_id,
                        start_frame_number=start_frame
                    ).first()

                    if existing_segment:
                        # If a segment with the same start_frame exists but the end_frame is different, update it
                        if float(existing_segment.end_frame_number) != end_frame:
                            existing_segment.end_frame_number = end_frame
                            existing_segment.save()
                            updated_segments.append(existing_segment)
                    else:
                        # If no existing segment matches, create a new one
                        new_entries.append(LabelRawVideoSegment(
                            video_id=video_id,
                            label_id=label_id,
                            start_frame_number=start_frame,
                            end_frame_number=end_frame
                        ))

            # Delete segments that are no longer present in the frontend data
            segments_to_delete = existing_segments.exclude(
                start_frame_number__in=[float(seg["start_frame_number"]) for seg in new_segments]
            )
            deleted_count = segments_to_delete.count()
            segments_to_delete.delete()

            # Insert new segments in bulk for efficiency
            if new_entries:
                LabelRawVideoSegment.objects.bulk_create(new_entries)

        # Return the updated, new, and deleted segment information
        print("------------------------------,",updated_segments,"-----------------------",new_segments,"_-------",deleted_count)
        return {
            "updated_segments": LabelSegmentSerializer(updated_segments, many=True).data,
            "new_segments": LabelSegmentSerializer(new_entries, many=True).data,
            "deleted_segments": deleted_count
        }





    

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

await import('https://cdn.jsdelivr.net/npm/axios/dist/axios.min.js');

const videoIdUpdate = 1;  // Change to actual video ID
const labelIdUpdate = 15; // Change to actual label ID

const updatedSegments = {
    video_id: videoIdUpdate,
    label_id: labelIdUpdate,
    segments: [
        { start_frame_number: 1, end_frame_number: 15 }  // Updated end_frame (was 10)
       // New segment
    ]
};

axios.put(`http://localhost:8000/api/video/${videoIdUpdate}/label/${labelIdUpdate}/update_segments/`, updatedSegments, {
    headers: {
        'Accept': 'application/json',
        'Content-Type': 'application/json'
    }
})
.then(response => {
    console.log(" Segments Updated Successfully:", response.data);
})
.catch(error => {
    console.error(" Error Updating Segments:", error.response ? error.response.data : error);
});
"""''