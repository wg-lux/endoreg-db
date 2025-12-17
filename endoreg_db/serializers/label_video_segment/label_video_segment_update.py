from django.db import transaction
from rest_framework import serializers

from endoreg_db.models import LabelVideoSegment, VideoPredictionMeta
from endoreg_db.serializers.label_video_segment import LabelVideoSegmentSerializer

import logging

logger = logging.getLogger(__name__)
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
        Validate that the input data contains a non-empty list of segments with valid frame numbers.
        
        Raises a validation error if any segment is missing required fields or if a segment's start frame exceeds its end frame.
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
        # Ensure validated_data exists
        if not hasattr(self, 'validated_data'):
            raise AssertionError("You must call `.is_valid()` before calling `.save()`.")

        video_id = self.validated_data["video_id"]
        label_id = self.validated_data["label_id"]
        new_segments_data = self.validated_data["segments"]

        prediction_meta_entry = VideoPredictionMeta.objects.filter(
            video_file_id=video_id
        ).first()
        
        if not prediction_meta_entry:
            raise serializers.ValidationError(
                {"error": "No prediction metadata found for this video."}
            )

        prediction_meta_id = prediction_meta_entry.pk

        # 1. Map existing segments for comparison and deletion logic
        existing_segments = LabelVideoSegment.objects.filter(
            video_file_id=video_id, label_id=label_id
        )
        
        # Use a dict to track segments by their start_frame (assuming start_frame is the unique anchor)
        existing_segments_dict = {
            float(seg.start_frame_number): seg for seg in existing_segments
        }
        
        # Track which start_frames we see in the new data to identify what to delete
        incoming_start_frames = set()
        updated_segments = []
        new_entries = []

        with transaction.atomic():
            for segment in new_segments_data:
                start_frame = float(segment["start_frame_number"])
                end_frame = float(segment["end_frame_number"])
                incoming_start_frames.add(start_frame)

                if start_frame in existing_segments_dict:
                    # Update if end_frame changed
                    existing_seg = existing_segments_dict[start_frame]
                    if float(existing_seg.end_frame_number) != end_frame:
                        existing_seg.end_frame_number = int(end_frame)
                        existing_seg.save()
                        updated_segments.append(existing_seg)
                else:
                    # Create new
                    new_entries.append(
                        LabelVideoSegment(
                            video_file_id=video_id,
                            label_id=label_id,
                            start_frame_number=start_frame,
                            end_frame_number=end_frame,
                            prediction_meta_id=prediction_meta_id,
                        )
                    )

            # 2. Deletion Logic: If it's in DB but NOT in incoming data, delete it
            existing_start_frames = set(existing_segments_dict.keys())
            frames_to_delete = existing_start_frames - incoming_start_frames
            
            deleted_count = 0
            if frames_to_delete:
                segs_to_delete = existing_segments.filter(start_frame_number__in=frames_to_delete)
                deleted_count = segs_to_delete.count()
                segs_to_delete.delete()

            # 3. Bulk Create
            if new_entries:
                LabelVideoSegment.objects.bulk_create(new_entries)
                # Note: bulk_create doesn't return IDs in all DB backends. 
                # If you need serialized data back for new_entries, 
                # you might need to re-fetch them.

        return {
            "updated_segments": LabelVideoSegmentSerializer(updated_segments, many=True).data,
            "new_segments": LabelVideoSegmentSerializer(new_entries, many=True).data,
            "deleted_segments": deleted_count,
        }