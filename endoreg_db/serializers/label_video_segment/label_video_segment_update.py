from django.db import transaction
from rest_framework import serializers

from endoreg_db.models import LabelVideoSegment, VideoPredictionMeta
from endoreg_db.serializers.label_video_segment import LabelVideoSegmentSerializer

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
        Synchronizes label segments in the database with the provided frontend data for a specific video and label.

        Compares incoming segments to existing database entries, updating segments with changed end frames, creating new segments as needed, and deleting segments that are no longer present. All changes are performed within a transaction to maintain consistency. Raises a validation error if no prediction metadata exists for the video.

        Returns:
            dict: Contains serialized updated segments, newly created segments, and the count of deleted segments.
        """

        video_id = self.validated_data["video_id"]
        label_id = self.validated_data["label_id"]
        new_segments = self.validated_data["segments"] # Remove new_keys assignment

        # Fetch the correct `prediction_meta_id` based on `video_id`
        prediction_meta_entry = VideoPredictionMeta.objects.filter(
            video_file_id=video_id # Changed from video_id to video_file_id
        ).first()
        if not prediction_meta_entry:
            raise serializers.ValidationError(
                {"error": "No prediction metadata found for this video."}
            )

        prediction_meta_id = (
            prediction_meta_entry.id
        )  # Get the correct prediction_meta_id

        existing_segments = LabelVideoSegment.objects.filter(
            video_file_id=video_id, label_id=label_id  # FIXED: video_file_id instead of video_id
        )

        # Convert existing segments into a dictionary for quick lookup
        # Key format: (start_frame_number, end_frame_number)
        existing_segments_dict = {
            (float(seg.start_frame_number), float(seg.end_frame_number)): seg
            for seg in existing_segments
        }

        # Prepare lists for batch processing
        # Initialize sets to track updates and new entries
        updated_segments = set()
        new_entries = []
        existing_keys = set()
        new_keys = set()

        # Iterate through the validated data to update or create label video segments
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
                        video_file_id=video_id, # Changed from video_id to video_file_id
                        label_id=label_id,
                        start_frame_number=start_frame,
                    ).first()

                    if existing_segment:
                        # If a segment with the same_start_frame exists but the end_frame is different, update it
                        if float(existing_segment.end_frame_number) != end_frame:
                            existing_segment.end_frame_number = end_frame
                            existing_segment.save()
                            updated_segments.append(existing_segment)
                    else: # Added else block to create new segment if not existing
                        new_entries.append(
                            LabelVideoSegment(
                                video_file_id=video_id, # Changed from video_id to video_file_id
                                label_id=label_id,
                                start_frame_number=start_frame,
                                end_frame_number=end_frame,
                                prediction_meta_id=prediction_meta_id,
                            )
                        )
                        print(
                            f" Adding new segment: Start {start_frame} → End {end_frame}"
                        )

            # Delete segments that are no longer present in the frontend data
            # Segments to delete are those in existing_keys but not in new_keys
            keys_to_delete = existing_keys - set((float(s['start_frame_number']), float(s['end_frame_number'])) for s in new_segments)
            segments_to_delete_ids = [existing_segments_dict[key].id for key in keys_to_delete]

            if segments_to_delete_ids:
                LabelVideoSegment.objects.filter(id__in=segments_to_delete_ids).delete()
                deleted_count = len(segments_to_delete_ids)
            else:
                deleted_count = 0

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
            "updated_segments": LabelVideoSegmentSerializer(
                updated_segments, many=True
            ).data,
            "new_segments": LabelVideoSegmentSerializer(new_entries, many=True).data,
            "deleted_segments": deleted_count,
        }