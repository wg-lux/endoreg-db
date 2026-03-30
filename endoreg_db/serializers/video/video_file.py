import importlib
from pathlib import Path
from typing import Any, TYPE_CHECKING, cast

from rest_framework import serializers

from ...models import VideoFile

cv2_mod: Any
try:
    cv2_mod = importlib.import_module("cv2")
except ImportError:
    cv2_mod = None
# from django.conf import settings

from django.conf import settings
from rest_framework.exceptions import ValidationError

if TYPE_CHECKING:
    from endoreg_db.models import VideoFile


class VideoFileSerializer(serializers.ModelSerializer):
    """
    Serializer that dynamically handles video retrieval and streaming.
    Ensures file returns the relative file path (not MEDIA_URL)
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
    original_file_name = serializers.CharField(read_only=True)
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
    def get_video_selection_field(self, obj: "VideoFile"):
        """
        Return the UUID of the video for use as a selection value in frontend dropdowns.

        Parameters:
            obj (Video): The video instance being serialized.

        Returns:
            str: The UUID of the video.
        """
        return obj.video_hash

    def get_video_url(
        self, obj
    ):  # when we serialize a RawVideoFile object (video metadata), the get_video_url method is automatically invoked by DRF
        """
        Return the absolute API URL for accessing the video file.

        If the video ID is invalid or the request context is missing, returns a dictionary with an error message.
        """
        if not obj.id:
            return {"error": "Invalid video ID"}

        request = self.context.get(
            "request"
        )  # Gets the request object (provided by DRF).
        if request:
            return request.build_absolute_uri(f"/api/media/videos/{obj.id}/stream/")

        return {"error": "Video URL not available"}

    def get_duration(self, obj: "VideoFile"):
        """
        Return the duration of the video in seconds, using the stored value if available or extracting it dynamically with OpenCV.

        If the duration is not present in the database, the method opens the video file, retrieves its frame count and frames per second (FPS), and calculates the duration. Returns `None` if the video cannot be opened or FPS is zero.
        """
        if hasattr(obj, "duration") and obj.duration:
            return (
                obj.duration
            )  # If duration is stored in the database, return it directly.

        # Dynamically extract duration if not stored
        video_path = obj.active_file.path
        if cv2_mod is None:
            return None

        cap = cv2_mod.VideoCapture(str(video_path))
        try:
            if not cap.isOpened():
                return None  # Error handling if video can't be opened

            fps = cap.get(cv2_mod.CAP_PROP_FPS)
            total_frames = cap.get(cv2_mod.CAP_PROP_FRAME_COUNT)

            return (
                round(total_frames / fps, 2) if fps > 0 else None
            )  # Return duration in seconds
        finally:
            cap.release()

    def get_file(self, obj: "VideoFile"):
        """
        Returns the relative file path of the active video file, or an error message if the file is missing or invalid.

        Parameters:
            obj (Video): The video instance whose file path is to be retrieved.

        Returns:
            str or dict: The relative file path as a string, or a dictionary with an error message if the file is missing or invalid.
        """
        if not obj.active_file:
            return {"error": "No file  associated with this entry"}
        # obj.active_file.name is an attribute of FieldFile that returns the file path as a string and name is not the database attribute, it is an attribute of Django’s FieldFile object that holds the file path as a string.
        file_name = getattr(obj.active_file, "name", None)
        if not isinstance(file_name, str) or not file_name.strip():
            return {"error": "Invalid file name"}

        return file_name.strip()  #  Only return the file path, no URL

    def get_full_video_path(self, obj: "VideoFile"):
        """
        Return the absolute filesystem path to the video's active file.

        If the file does not exist or an error occurs during path construction, returns a dictionary with an error message.
        """
        if not obj.active_file:
            return {"error": "No video file associated with this entry"}

        try:
            # Use the active_file_path property which handles both processed and raw files
            if hasattr(obj, "active_file_path") and obj.active_file_path:
                full_path = obj.active_file_path
                return (
                    str(full_path)
                    if full_path.exists()
                    else {"error": f"file not found at: {full_path}"}
                )
            else:
                # Fallback: construct path manually
                file_name = getattr(obj.active_file, "name", None)
                if not isinstance(file_name, str):
                    return {"error": "Video file path is empty or invalid"}
                video_relative_path = file_name.strip()
                if not video_relative_path:
                    return {"error": "Video file path is empty or invalid"}

                # Construct the path using the file's actual path
                full_path_str = str(obj.active_file.path)
                return (
                    full_path_str
                    if Path(full_path_str).exists()
                    else {"error": f"file not found at: {full_path_str}"}
                )

        except Exception as e:
            return {"error": f"Error constructing file path: {str(e)}"}

    def get_sequences(self, obj: "VideoFile"):
        """
        Retrieve frame sequences for each label from the video object.

        Returns:
            dict: A mapping of label names to lists of frame ranges, or an error message if no sequences are found.
        """
        return obj.sequences or {
            "error": "no sequence found, check database first"
        }  #  Get from sequences, return {} if missing

    def get_label_names(self, obj: "VideoFile"):
        """
        Return a list of label names present in the video's frame sequences.

        Parameters:
            obj (Video): The video instance to extract label names from.

        Returns:
            list[str]: List of label names, or an empty list if no sequences are found.
        """
        sequences = self.get_sequences(obj)
        return list(sequences.keys()) if sequences else []

    def get_label_time_segments(self, obj: "VideoFile"):
        """
        Convert frame sequences for each label into time segments with frame-level metadata.

        For each label in the video, this method generates a list of time segments based on frame index ranges, converting them to seconds using the video's FPS. Each segment includes the raw frame indices, start and end times in seconds, and detailed information for each frame in the segment, such as filename, full file path, and a placeholder for predictions.

        Returns:
            dict: A dictionary mapping each label to its list of time segments and associated frame metadata.
        """

        fps = getattr(obj, "fps", None)
        if fps is None and hasattr(obj, "get_fps"):
            fps = obj.get_fps()

        if not fps or fps <= 0:
            # Strict by default — only use fallback if explicitly enabled and > 0
            if (
                bool(getattr(settings, "VIDEO_ALLOW_FPS_FALLBACK", False))
                and float(cast(Any, getattr(settings, "VIDEO_DEFAULT_FPS", 0))) > 0
            ):
                fps = float(cast(Any, getattr(settings, "VIDEO_DEFAULT_FPS", 0)))
            else:
                raise ValidationError(
                    {
                        "label_time_segments": "FPS unavailable — cannot calculate time segments",
                        "video_id": getattr(obj, "id", None),
                    }
                )

        sequences = self.get_sequences(obj)  # Fetch sequence data
        frame_dir = Path(
            str(obj.frame_dir or "")
        )  # Get the correct directory from the model
        time_segments: dict[str, dict[str, Any]] = {}

        for label, frame_ranges in sequences.items():
            label_times: list[dict[str, Any]] = []
            frame_predictions: dict[int, Any] = {}  # TODO currently empty

            for frame_range in frame_ranges:
                if len(frame_range) != 2:
                    continue  # Skip invalid frame ranges

                start_frame, end_frame = frame_range  # Raw frame indices from DB
                start_time = start_frame / fps  # Convert frame index to seconds
                end_time = end_frame / fps  # Convert frame index to seconds

                frame_data: dict[int, dict[str, Any]] = {}

                # Fetch predictions for frames within this range
                for frame_num in range(start_frame, end_frame + 1):
                    frame_filename = (
                        f"frame_{str(frame_num).zfill(7)}.jpg"  # Frame filename format
                    )
                    frame_path = frame_dir / frame_filename  # Full path to the frame

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
