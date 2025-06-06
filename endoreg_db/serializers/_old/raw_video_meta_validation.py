from pathlib import Path
from rest_framework import serializers
from django.conf import settings
from ...models import VideoFile, SensitiveMeta
import cv2


class VideoFileForMetaSerializer(serializers.ModelSerializer):
    """
    Serializer to fetch video metadata along with linked `SensitiveMeta` details.
    Ensures that Vue.js can properly access and play videos.
    """

    # Fetch patient name and DOB from `SensitiveMeta` table
    patient_first_name = serializers.CharField(source="sensitive_meta.patient_first_name", read_only=True)
    patient_last_name = serializers.CharField(source="sensitive_meta.patient_last_name", read_only=True)
    patient_dob = serializers.CharField(source="sensitive_meta.patient_dob", read_only=True)
    examination_date = serializers.CharField(source="sensitive_meta.examination_date", read_only=True)

    duration = serializers.SerializerMethodField()


    # `video_url` generates the full URL where Vue.js can fetch & play the video
    video_url = serializers.SerializerMethodField()

    # `full_video_path` dynamically constructs the absolute path using MEDIA_ROOT
    full_video_path = serializers.SerializerMethodField()
    
    file = serializers.SerializerMethodField()

    class Meta:
        model = VideoFile
        fields = ['id', 'original_file_name', 'file', 'video_url', 'full_video_path', 
                  'sensitive_meta_id', 'patient_first_name', 'patient_last_name', 'patient_dob', 'examination_date','duration']

    @staticmethod
    def get_next_video(last_id=None):
        """
        Fetches the first or next available video.
        Returns a model instance if found, else returns None.
        """
        query_filter = {} if last_id is None else {"id__gt": int(last_id)}

        # Get next available video
        video_entry = VideoFile.objects.select_related("sensitive_meta").filter(**query_filter).order_by('id').first()

        return video_entry  # Always return model instance or None

    def get_video_url(self, obj):
        """
        Returns the absolute URL for streaming the processed video file, or None if unavailable.
        
        The URL includes an "api/" prefix and is constructed only if a request context and a processed video file are present.
        """
        request = self.context.get('request')
        if request and obj.processed_file:
            print("---------------------------:",obj.processed_file)
            return request.build_absolute_uri(f"/api/video/{obj.id}/")  # Added api/ prefix
        return None  # Return None instead of an error dictionary
    
    def get_file(self, obj):
        """
        Returns the relative file path of the processed video, excluding the `/media/` prefix.
        
        Returns:
            The relative file path as a string if available, otherwise None.
        """
        if not obj.processed_file:
            return None  # No file associated

        video_relative_path = str(obj.processed_file.name).strip()
        return video_relative_path if video_relative_path else None  # Avoids errors if the file path is empty

    def get_full_video_path(self, obj):
        """
        Constructs the full absolute file path using `settings.MEDIA_ROOT` 
        and the `file` field from the database.
        """
        if not obj.processed_file:
            return None  # No file associated

        video_relative_path = str(obj.processed_file.name).strip()
        full_path = Path(settings.MEDIA_ROOT) / video_relative_path

        return str(full_path) if full_path.exists() else None  # Return path or None if not found

    def get_duration(self, obj):
        """
        Returns the total duration of the video in seconds.
        - If stored in the database, use it.
        - Otherwise, calculate dynamically using OpenCV.
        """
        
        # Step 1: Check if the `duration` field is already stored in the database.
        if hasattr(obj, "duration") and obj.duration:
            return obj.duration  # If duration exists in the database, return it immediately.

        # Step 2: Get the actual video file path
        video_path = obj.processed_file.path if obj.processed_file else None  # Extracts the file path if it exists

        # Step 3: Validate if the file exists
        if not video_path or not Path(video_path).exists():
            return None  # Return None if the file path is invalid or missing

        # Step 4: Open the video file using OpenCV
        cap = cv2.VideoCapture(video_path)  # Load the video file

        if not cap.isOpened():
            return None  # If OpenCV fails to open the video, return None (invalid or corrupted file)

        # Step 5: Extract FPS (Frames Per Second)
        fps = cap.get(cv2.CAP_PROP_FPS)  # Get the number of frames per second
        print("here is the fps------------", fps)  # Debugging print to check FPS value

        # Step 6: Get Total Number of Frames
        total_frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)  # Get the total number of frames in the video

        # Step 7: Release the video file from OpenCV memory
        cap.release()  # Free up system memory

        # Step 8: Calculate and Return Video Duration
        return round(total_frames / fps, 2) if fps > 0 else None  # Divide frames by FPS to get duration (seconds)
        



class SensitiveMetaUpdateSerializer(serializers.ModelSerializer):
    """
    Serializer to update patient information in the `SensitiveMeta` table.
    Handles validation and saves the data directly in the serializer.
    """

    sensitive_meta_id = serializers.IntegerField(write_only=True)  # Needed for lookup but not included in response

    class Meta:
        model = SensitiveMeta
        fields = ['sensitive_meta_id', 'patient_first_name', 'patient_last_name', 'patient_dob', 'examination_date']

    def validate(self, data):
        """
        Validate input data before updating.
        """
        errors = {}

        # Ensure the SensitiveMeta ID exists
        sensitive_meta_id = data.get("sensitive_meta_id")
        if not SensitiveMeta.objects.filter(id=sensitive_meta_id).exists():
            raise serializers.ValidationError({"sensitive_meta_id": "SensitiveMeta entry not found."})

        # Validate each field separately
        if 'patient_first_name' in data and (data['patient_first_name'] is None or data['patient_first_name'].strip() == ""):
            errors['patient_first_name'] = "First name cannot be empty."

        if 'patient_last_name' in data and (data['patient_last_name'] is None or data['patient_last_name'].strip() == ""):
            errors['patient_last_name'] = "Last name cannot be empty."

        if 'patient_dob' in data and not data['patient_dob']:
            errors['patient_dob'] = "Date of birth is required."

        if 'examination_date' in data and not data['examination_date']:
            errors['examination_date'] = "Examination date is required."

        if errors:
            raise serializers.ValidationError(errors)  # Raise errors before saving

        return data

    def update(self, instance, validated_data):
        """
        Update the SensitiveMeta entry directly inside the serializer.
        """
        # Remove `sensitive_meta_id` before updating
        validated_data.pop("sensitive_meta_id", None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)  # Set attributes dynamically

        instance.save()  # Save changes
        return instance  # Return updated instance
