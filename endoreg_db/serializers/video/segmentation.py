from pathlib import Path
from rest_framework import serializers
from ...models import VideoFile, LabelVideoSegment # Added VideoPredictionMeta
import cv2

# from django.conf import settings
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.models import Video
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
    # classification_data = serializers.SerializerMethodField() #data from database (smooth prediction values but currently hardcoded one)
    # The Meta class tells Django what data to include when serializing a RawVideoFile object.
    sequences = serializers.SerializerMethodField()
    label_names = serializers.SerializerMethodField()
    # Convert selected label frames into time segments (seconds)
    label_time_segments = serializers.SerializerMethodField()
    # label_predictions = serializers.SerializerMethodField()
    original_file_name = serializers.CharField()
    duration = serializers.SerializerMethodField()

    class Meta:
        model = VideoFile
        # he fields list defines which data should be included in the API response.
        fields = [
            "id",
            "original_file_name",
            "file",
            "duration",
            "video_url",
            "full_video_path",
            "video_selection_field",
            "label_names",
            "sequences",
            "label_time_segments",
        ]  #  Ensure computed fields are included

    # @staticmethod #using @staticmethod makes it reusable without needing to create a serializer instance.
    #  Without @staticmethod, you would need to instantiate the serializer before calling the method, which is unnecessary her
    def get_video_selection_field(self, obj:"Video"):
        """
        Returns the field used for video selection in the frontend dropdown.
        Currently, it shows the video ID, but this can be changed easily later.
        """
        return obj.uuid

    def get_video_url(
        self, obj
    ):  # when we serialize a RawVideoFile object (video metadata), the get_video_url method is automatically invoked by DRF
        """
        Returns the absolute API URL for accessing the video file.
        
        If the video ID is invalid or the request context is missing, returns a dictionary with an error message.
        """
        if not obj.id:
            return {"error": "Invalid video ID"}

        request = self.context.get(
            "request"
        )  # Gets the request object (provided by DRF).
        if request:
            return request.build_absolute_uri(f"/api/video/{obj.id}/")  # Added api/ prefix

        return {"error": "Video URL not avalaible"}

    def get_duration(self, obj:"Video"):
        """
        Returns the total duration of the video in seconds.
        If duration is not stored in the database, it extracts it dynamically using OpenCV.
        """
        if hasattr(obj, "duration") and obj.duration:
            return (
                obj.duration
            )  # If duration is stored in the database, return it directly.

        # Dynamically extract duration if not stored
        video_path = obj.active_file.path
        cap = cv2.VideoCapture(video_path)

        if not cap.isOpened():
            return None  # Error handling if video can't be opened

        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        cap.release()

        return (
            round(total_frames / fps, 2) if fps > 0 else None
        )  # Return duration in seconds

    def get_file(self, obj:"Video"):
        """
        Ensures the file field returns only the relative path, adn also validates it
        """
        if not obj.active_file:
            return {"error": "No file  associated with this entry"}
        # obj.active_file.name is an attribute of FieldFile that returns the file path as a string and name is not the database attribute, it is an attribute of Django’s FieldFile object that holds the file path as a string.
        if not hasattr(obj.active_file, "name") or not obj.active_file.name.strip():
            return {"error": "Invalid file name"}

        return str(
            obj.active_file.name
        ).strip()  #  Only return the file path, no URL,#obj.active_file returning a FieldFile object instead of a string

    def get_full_video_path(self, obj:"Video"):
        """
        Returns the absolute file path to the video's active file.
        
        If the file does not exist or an error occurs during path construction, returns a dictionary with an error message.
        """
        if not obj.active_file:
            return {"error": "No video file associated with this entry"}

        try:
            # Use the active_file_path property which handles both processed and raw files
            if hasattr(obj, 'active_file_path') and obj.active_file_path:
                full_path = obj.active_file_path
                return str(full_path) if full_path.exists() else {"error": f"file not found at: {full_path}"}
            else:
                # Fallback: construct path manually
                video_relative_path = str(obj.active_file.name).strip()
                if not video_relative_path:
                    return {"error": "Video file path is empty or invalid"}
                
                # Construct the path using the file's actual path
                full_path = obj.active_file.path
                return str(full_path) if Path(full_path).exists() else {"error": f"file not found at: {full_path}"}
                
        except Exception as e:
            return {"error": f"Error constructing file path: {str(e)}"}

    def get_sequences(self, obj:"Video"):
        """
        Retrieves the frame sequences for each label from the video object.
        
        Returns:
            A dictionary mapping label names to lists of frame ranges. If no sequences are found, returns a dictionary with an error message.
        """
        return obj.sequences or {
            "error": "no sequence found, check database first"
        }  #  Get from sequences, return {} if missing

    def get_label_names(self, obj:"Video"):
        """
        Extracts only label names from the sequences data.
        Example Output:
        ["outside", "needle", "kolonpolyp"]
        """
        sequences = self.get_sequences(obj)
        return list(sequences.keys()) if sequences else []

    def get_label_time_segments(self, obj:"Video"):
        """
        Converts frame sequences of a selected label into time segments in seconds.
        Also retrieves frame-wise predictions for the given label.

        Includes:
        - Frame index
        - Corresponding frame filename (frame_0000001.jpg)
        - Full frame file path for frontend access
        - segment_start and segment_end (in frame index format, not divided by FPS)
        """

        fps = (
            obj.fps
            if hasattr(obj, "fps") and obj.fps is not None
            else obj.get_fps()
            if hasattr(obj, "get_fps") and obj.get_fps() is not None
            else 50
        )

        print("here is fps::::::::::::::::::.-----------::::::", fps)
        sequences = self.get_sequences(obj)  # Fetch sequence data


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

                        frame_filename = f"frame_{str(frame_num).zfill(7)}.jpg"  # Frame filename format
                        frame_path = (
                            frame_dir / frame_filename
                        )  # Full path to the frame

                        frame_data[frame_num] = {
                            "frame_filename": frame_filename,
                            "frame_file_path": str(frame_path),
                            "predictions": None,
                        }

                # Append the converted time segment
                label_times.append(
                    {
                        "segment_start": start_frame,  # Raw start frame (not divided by FPS)
                        "segment_end": end_frame,  # Raw end frame (not divided by FPS)
                        "start_time": round(
                            start_time, 2
                        ),  # Converted start time in seconds
                        "end_time": round(end_time, 2),  # Converted end time in seconds
                        "frames": frame_data,  # Attach frame details
                    }
                )

            # Store time segments and frame_predictions under the label
            time_segments[label] = {
                "time_ranges": label_times,
                "frame_predictions": frame_predictions,  # Ensure frame_predictions is correctly assigned
            }

        return time_segments

