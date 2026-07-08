from __future__ import annotations

import importlib
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TYPE_CHECKING, Protocol, cast

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

from endoreg_db.config.env import DEFAULT_VIDEO_FPS
from endoreg_db.services.video_files import get_active_video_file, get_video_fps
from endoreg_db.utils.media_urls import (
    build_absolute_media_url,
    build_video_hls_playlist_path,
)
from endoreg_db.utils.storage import ensure_local_file
from endoreg_db.utils.storage_streaming import maybe_local_plaintext_path

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile

    _ModelSerializerMeta = serializers.ModelSerializer.Meta
else:
    _ModelSerializerMeta = object


class _VideoFileSerializerLike(Protocol):
    id: int
    video_hash: str
    frame_dir: str
    duration: float | None
    fps: float | None
    sequences: dict[str, list[Sequence[int]]]

    @property
    def original_file_name(self) -> str | None: ...


class VideoFileSerializer(serializers.ModelSerializer[VideoFile]):
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

    class Meta(_ModelSerializerMeta):
        model = VideoFile  # pyright: ignore[reportAssignmentType]
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
    def get_video_selection_field(self, obj: _VideoFileSerializerLike) -> str:
        """
        Return the UUID of the video for use as a selection value in frontend dropdowns.

        Parameters:
            obj (Video): The video instance being serialized.

        Returns:
            str: The UUID of the video.
        """
        return obj.video_hash

    def get_video_url(
        self, obj: _VideoFileSerializerLike
    ) -> object:  # when we serialize a RawVideoFile object (video metadata), the get_video_url method is automatically invoked by DRF
        """
        Return the absolute API HLS playlist URL for the processed video.

        If the video ID is invalid or the request context is missing, returns a dictionary with an error message.
        """
        if not obj.id:
            return {"error": "Invalid video ID"}

        request = self.context.get(
            "request"
        )  # Gets the request object (provided by DRF).
        if request:
            return build_absolute_media_url(
                request,
                build_video_hls_playlist_path(obj.id, file_type="processed"),
            )

        return {"error": "Video URL not available"}

    def get_duration(self, obj: _VideoFileSerializerLike) -> float | None:
        """
        Return the duration of the video in seconds, using the stored value if available or extracting it dynamically with OpenCV.

        If the duration is not present in the database, the method opens the video file, retrieves its frame count and frames per second (FPS), and calculates the duration. Returns `None` if the video cannot be opened or FPS is zero.
        """
        if hasattr(obj, "duration") and obj.duration:
            return (
                obj.duration
            )  # If duration is stored in the database, return it directly.

        if cv2_mod is None:
            return None

        try:
            with ensure_local_file(
                get_active_video_file(cast(VideoFile, obj))
            ) as video_path:
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
        except Exception:
            return None

    def get_file(self, obj: _VideoFileSerializerLike) -> object:
        """
        Returns the relative file path of the active video file, or an error message if the file is missing or invalid.

        Parameters:
            obj (Video): The video instance whose file path is to be retrieved.

        Returns:
            str or dict: The relative file path as a string, or a dictionary with an error message if the file is missing or invalid.
        """
        try:
            active_file = get_active_video_file(cast(VideoFile, obj))
        except ValueError:
            return {"error": "No file  associated with this entry"}
        file_name = getattr(active_file, "name", None)
        if not isinstance(file_name, str) or not file_name.strip():
            return {"error": "Invalid file name"}

        return file_name.strip()  #  Only return the file path, no URL

    def get_full_video_path(self, obj: _VideoFileSerializerLike) -> object:
        """
        Return the absolute filesystem path to the video's active file.

        If the file does not exist or an error occurs during path construction, returns a dictionary with an error message.
        """
        try:
            active_file = get_active_video_file(cast(VideoFile, obj))
        except ValueError:
            return {"error": "No video file associated with this entry"}

        try:
            local_path = maybe_local_plaintext_path(active_file)
            if local_path is None:
                return {"error": "Local plaintext path unavailable; use video_url"}
            return (
                str(local_path)
                if local_path.exists()
                else {"error": f"file not found at: {local_path}"}
            )

        except Exception as e:
            return {"error": f"Error constructing file path: {str(e)}"}

    def get_sequences(self, obj: _VideoFileSerializerLike) -> object:
        """
        Retrieve frame sequences for each label from the video object.

        Returns:
            dict: A mapping of label names to lists of frame ranges, or an error message if no sequences are found.
        """
        return obj.sequences or {
            "error": "no sequence found, check database first"
        }  #  Get from sequences, return {} if missing

    def get_label_names(self, obj: _VideoFileSerializerLike) -> list[str]:
        """
        Return a list of label names present in the video's frame sequences.

        Parameters:
            obj (Video): The video instance to extract label names from.

        Returns:
            list[str]: List of label names, or an empty list if no sequences are found.
        """
        return list(obj.sequences.keys()) if obj.sequences else []

    def get_label_time_segments(self, obj: _VideoFileSerializerLike) -> object:
        """
        Convert frame sequences for each label into time segments with frame-level metadata.

        For each label in the video, this method generates a list of time segments based on frame index ranges, converting them to seconds using the video's FPS. Each segment includes the raw frame indices, start and end times in seconds, and detailed information for each frame in the segment, such as filename, full file path, and a placeholder for predictions.

        Returns:
            dict: A dictionary mapping each label to its list of time segments and associated frame metadata.
        """

        fps = getattr(obj, "fps", None)
        if fps is None:
            fps = get_video_fps(cast(VideoFile, obj))

        if not fps or fps <= 0:
            # Strict by default — only use fallback if explicitly enabled and > 0
            if (
                bool(getattr(settings, "VIDEO_ALLOW_FPS_FALLBACK", False))
                and float(
                    cast(Any, getattr(settings, "VIDEO_DEFAULT_FPS", DEFAULT_VIDEO_FPS))
                )
                > 0
            ):
                fps = float(
                    cast(Any, getattr(settings, "VIDEO_DEFAULT_FPS", DEFAULT_VIDEO_FPS))
                )
            else:
                raise ValidationError(
                    {
                        "label_time_segments": "FPS unavailable — cannot calculate time segments",
                        "video_id": getattr(obj, "id", None),
                    }
                )

        sequences = obj.sequences
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
