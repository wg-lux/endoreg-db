from pathlib import Path
from rest_framework import serializers
from ...models import VideoFile, Label, LabelVideoSegment, VideoPredictionMeta
import cv2
from django.db import transaction

# from django.conf import settings
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.models import VideoFile
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
    def get_video_selection_field(self, obj:"VideoFile"):
        """
        Returns the field used for video selection in the frontend dropdown.
        Currently, it shows the video ID, but this can be changed easily later.
        """
        return obj.id

    def get_video_url(
        self, obj
    ):  # when we serialize a RawVideoFile object (video metadata), the get_video_url method is automatically invoked by DRF
        """
        Returns the absolute API URL for accessing the video resource.
        
        If the video ID or request context is missing, returns a dictionary with an error message.
        """
        if not obj.id:
            return {"error": "Invalid video ID"}

        request = self.context.get(
            "request"
        )  # Gets the request object (provided by DRF).
        if request:
            return request.build_absolute_uri(f"/api/video/{obj.id}/")  # Added api/ prefix

        return {"error": "Video URL not avalaible"}

    def get_duration(self, obj:"VideoFile"):
        """
        Returns the total duration of the video in seconds.
        If duration is not stored in the database, it extracts it dynamically using OpenCV.
        """
        if hasattr(obj, "duration") and obj.duration:
            return (
                obj.duration
            )  # If duration is stored in the database, return it directly.

        # Dynamically extract duration if not stored
        video_path = obj.active_file_path
        cap = cv2.VideoCapture(video_path)

        if not cap.isOpened():
            return None  # Error handling if video can't be opened

        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        cap.release()

        return (
            round(total_frames / fps, 2) if fps > 0 else None
        )  # Return duration in seconds

    def get_file(self, obj:"VideoFile"):
        """
        Ensures the file field returns only the relative path, adn also validates it
        """
        if not obj.active_file or not obj.active_file_path.name:
            return {"error": "No file  associated with this entry"}


        return str(
            obj.active_file_path.name
        ).strip()  #  Only return the file path, no URL,#obj.file returning a FieldFile object instead of a string

    def get_full_video_path(self, obj:"VideoFile"):
        """
        Constructs the absolute file path dynamically.
        - Uses the actual storage directory (`/home/admin/test-data/`)
        """
        from ...models.utils import STORAGE_DIR
        if not obj.active_file:
            return {"error": "No video file associated with this entry"}

        video_relative_path = str(obj.active_file_path).strip()  #  Convert FieldFile to string
        if not video_relative_path:
            return {
                "error": "Video file path is empty or invalid"
            }  #  none might cause, 500 error, Handle edge case where the file name is empty

        full_path = STORAGE_DIR / video_relative_path

        return (
            str(full_path)
            if full_path.exists()
            else {"error": f"file not found at: {full_path}"}
        )

    def get_sequences(self, obj:"VideoFile"):
        """
        Extracts the sequences field from the RawVideoFile model.
        Example Output:
        {
            "outside": [[1, 32], [123, 200]],
            "needle": [[36, 141]],
            "kolonpolyp": [[91, 126]]
        }
        """
        return obj.sequences or {
            "error": "no sequence found, check database first"
        }  #  Get from sequences, return {} if missing

    def get_label_names(self, obj:"VideoFile"):
        """
        Extracts only label names from the sequences data.
        Example Output:
        ["outside", "needle", "kolonpolyp"]
        """
        sequences = self.get_sequences(obj)
        return list(sequences.keys()) if sequences else []

    def get_label_time_segments(self, obj:"VideoFile"):
        """
        Converts label frame sequences into time-based segments and retrieves frame-wise predictions.
        
        For each label in the video, returns a dictionary containing time ranges (with start/end frames and times in seconds) and detailed frame information, including filenames, file paths, and associated predictions. Returns an error dictionary if prediction data is not a list.
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
        # readable_predictions = "FIXME"
        #readable_predictions = obj.readable_predictions  # Predictions from DB
        #readable_predictions = getattr(obj, "readable_predictions", [])

         # Check predictions
        # Fix: Safely get readable_predictions with proper fallback
        readable_predictions = getattr(obj, "readable_predictions", [])
        
        # If readable_predictions is None or empty, create a default structure
        if not readable_predictions:
            # Create empty predictions list based on video duration/fps if available
            try:
                # Try to estimate frame count for empty predictions
                total_frames = int(fps * 60)  # Default to 60 seconds worth of frames
                readable_predictions = [{"label": "unknown", "confidence": 0.0} for _ in range(total_frames)]
            except:
                readable_predictions = []

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
                    if (
                        0 <= frame_num < len(readable_predictions)
                    ):  # Ensure index is valid
                        frame_filename = f"frame_{str(frame_num).zfill(7)}.jpg"  # Frame filename format
                        frame_path = (
                            frame_dir / frame_filename
                        )  # Full path to the frame

                        frame_data[frame_num] = {
                            "frame_filename": frame_filename,
                            "frame_file_path": str(frame_path),
                            "predictions": readable_predictions[frame_num],
                        }

                        # Store frame-wise predictions in frame_predictions
                        frame_predictions[frame_num] = readable_predictions[frame_num]

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


class VideoListSerializer(serializers.ModelSerializer):
    """
    Enhanced serializer to return video metadata for dashboard statistics.
    Includes status information and user assignments for proper dashboard display.
    """
    status = serializers.SerializerMethodField()
    assignedUser = serializers.SerializerMethodField()
    anonymized = serializers.SerializerMethodField()

    class Meta:
        model = VideoFile
        fields = ["id", "original_file_name", "status", "assignedUser", "anonymized"]

    def get_status(self, obj):
        """
        Returns the processing status of a video as 'completed', 'in_progress', or 'available'.
        
        A video is 'completed' if prediction sequences exist, 'in_progress' if frames have been extracted but no sequences are present, and 'available' if neither condition is met.
        """
        if hasattr(obj, 'sequences') and obj.sequences:
            return 'completed'
        elif hasattr(obj, 'frames_extracted') and obj.frames_extracted:
            return 'in_progress'
        else:
            return 'available'
    
    def get_assignedUser(self, obj):
        """
        Retrieves the user assigned to the video from its prediction metadata.
        
        Returns:
            The assigned user object if available; otherwise, None.
        """
        # Check if there's a prediction meta with user assignment
        try:
            from ...models import VideoPredictionMeta
            prediction_meta = VideoPredictionMeta.objects.filter(video_file=obj).first()
            if prediction_meta and hasattr(prediction_meta, 'assigned_user'):
                return prediction_meta.assigned_user
        except:
            pass
        return None
    
    def get_anonymized(self, obj):
        """
        Returns True if the video has been anonymized, otherwise False.
        
        Checks for the presence and truthiness of the 'anonymized' attribute on the video object.
        """
        return hasattr(obj, 'anonymized') and bool(obj.anonymized)
    

class LabelSerializer(serializers.ModelSerializer):
    """
    Serializer for fetching labels from the `endoreg_db_label` table.
    Includes `id` (for backend processing) and `name` (for dropdown display in Vue.js).
    """

    class Meta:
        model = Label
        fields = ["id", "name"]


class LabelSegmentSerializer(serializers.ModelSerializer):
    """
    Serializer for retrieving label segments from `endoreg_db_labelrawvideosegment`.
    """

    class Meta:
        model = LabelVideoSegment
        fields = [
            "id",
            "video_id",
            "label_id",
            "start_frame_number",
            "end_frame_number",
        ]





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
                raise serializers.ValidationError(
                    "Each segment must have `start_frame_number` and `end_frame_number`."
                )

            if segment["start_frame_number"] > segment["end_frame_number"]:
                raise serializers.ValidationError(
                    "Start frame must be less than or equal to end frame."
                )

        return data

    def save(self):
        """
        Synchronizes label segments with updated frontend data.
        
        This method compares the incoming segments with the current database entries for a given video and label.
        It updates segments with modified end frame numbers, inserts new segments, and deletes existing segments
        that are not present in the provided data. All operations are performed within a transaction to ensure
        database consistency. A validation error is raised if no prediction metadata is found for the video.
        
        Returns:
            dict: A dictionary containing serialized updated segments, serialized new segments, and the count
                  of deleted segments.
        """

        video_id = self.validated_data["video_id"]
        label_id = self.validated_data["label_id"]
        new_segments = self.validated_data["segments"]

        # Fetch the correct `prediction_meta_id` based on `video_id`
        prediction_meta_entry = VideoPredictionMeta.objects.filter(
            video_file_id=video_id
        ).first()
        if not prediction_meta_entry:
            raise serializers.ValidationError(
                {"error": "No prediction metadata found for this video."}
            )

        prediction_meta_id = (
            prediction_meta_entry.id
        )  # Get the correct prediction_meta_id

        existing_segments = LabelVideoSegment.objects.filter(
            video_file_id=video_id, label_id=label_id
        )

        # Convert existing segments into a dictionary for quick lookup
        # Key format: (start_frame_number, end_frame_number)
        existing_segments_dict = {
            (float(seg.start_frame_number), float(seg.end_frame_number)): seg
            for seg in existing_segments
        }


        # Start a transaction to ensure database consistency
        updated_segments = []
        new_entries = []
        existing_keys = set(existing_segments_dict.keys())
        _new_keys = set(
            (float(seg["start_frame_number"]), float(seg["end_frame_number"]))
            for seg in new_segments
        )

        print(f" Before Update: Found {existing_segments.count()} existing segments.")
        print(f" New Segments Received: {len(new_segments)}")
        print(f" Using prediction_meta_id: {prediction_meta_id}")

        with transaction.atomic():
            for segment in new_segments:
                start_frame = float(segment["start_frame_number"])
                end_frame = float(segment["end_frame_number"])

                if (start_frame, end_frame) in existing_keys:
                    # If segment with exact start_frame and end_frame already exists, no change is needed
                    continue
                else:
                    # Check if a segment exists with the same start_frame but different end_frame
                    existing_segment = LabelVideoSegment.objects.filter(
                        video_file_id=video_id,
                        label_id=label_id,
                        start_frame_number=start_frame,
                    ).first()

                    if existing_segment:
                        # If a segment with the same start_frame exists but the end_frame is different, update it
                        if float(existing_segment.end_frame_number) != end_frame:
                            existing_segment.end_frame_number = end_frame
                            existing_segment.save()
                            updated_segments.append(existing_segment)
                    else:
                        # If no existing segment matches, create a new one
                        new_entries.append(
                            LabelVideoSegment(
                                video_file_id=video_id,
                                label_id=label_id,
                                start_frame_number=start_frame,
                                end_frame_number=end_frame,
                                prediction_meta_id=prediction_meta_id,  # Assign correct prediction_meta_id
                            )
                        )

            # Delete segments that are no longer present in the frontend data
            segments_to_delete = existing_segments.exclude(
                start_frame_number__in=[
                    float(seg["start_frame_number"]) for seg in new_segments
                ]
            )
            deleted_count = segments_to_delete.count()
            segments_to_delete.delete()

            # Insert new segments in bulk for efficiency
            if new_entries:
                LabelVideoSegment.objects.bulk_create(new_entries)

        # Return the updated, new, and deleted segment information
        print(
            "------------------------------,",
            updated_segments,
            "-----------------------",
            new_segments,
            "_-------",
            deleted_count,
        )
        print(
            f" After Update: Updated {len(updated_segments)} segments, Added {len(new_entries)}, Deleted {deleted_count}"
        )

        return {
            "updated_segments": LabelSegmentSerializer(
                updated_segments, many=True
            ).data,
            "new_segments": LabelSegmentSerializer(new_entries, many=True).data,
            "deleted_segments": deleted_count,
        }