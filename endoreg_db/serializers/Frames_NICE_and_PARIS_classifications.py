from rest_framework import serializers
from endoreg_db.models import Label, LabelRawVideoSegment, RawVideoFile
from collections import defaultdict
import numpy as np
from itertools import combinations

# === CONFIGURABLE PARAMETERS - ForNiceClassificationSerializer ===
POLYP_LABEL_NAME = "polyp"
CHROMO_LABEL_NAME = "digital_chromo_endoscopy"
FPS = 50  # Frames per second
# Sequence-level filtering
MIN_SEQUENCE_GAP_SECONDS = 10  # Enforce diversity between sequences
MIN_SEQUENCE_GAP_FRAMES = FPS * MIN_SEQUENCE_GAP_SECONDS  # Convert to frame count
# Minimum length of a segment in seconds
MIN_SEGMENT_LENGTH_SECONDS = 2
MIN_SEGMENT_LENGTH_FRAMES = FPS * MIN_SEGMENT_LENGTH_SECONDS

# Frame-level selection within a sequence
MIN_FRAME_GAP_SECONDS = 2  # Minimum gap between selected frames in seconds
MIN_FRAME_GAP_IN_SEQUENCE = FPS * MIN_FRAME_GAP_SECONDS  # Convert to frame count
FRAMES_PER_SEQUENCE = 5  # Number of frames to select per matched sequence


# === Frame Filtering Rules ===
# All rules must return True to accept the frame
FRAME_SELECTION_RULES = [
    lambda pred: pred.get("low_quality", 1.0) < 0.1,
    lambda pred: pred.get("outside", 1.0) < 0.1,
    # Add more rules easily here
]


class ForNiceClassificationSerializer(serializers.Serializer):

    def __init__(self, videos, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.videos = videos
    
    def get_label_id_by_name(self, label_name):
        """
        Fetch the label ID by name. Raises a friendly ValidationError if not found.
        """
        print("-------------------------------")
        print(f"[DEBUG] Requested label lookup for name: '{label_name}'")
        if not label_name:
            raise serializers.ValidationError({
                "error": "Label name is required but was not provided."
            })

        try:
            label = Label.objects.get(name=label_name)
            return label.id
        except Label.DoesNotExist:
            raise serializers.ValidationError({
                "error": f"Label with name '{label_name}' does not exist. Please check spelling or label availability."
            })

    
    def get_matching_sequences(self, video_id):
        """
        Find matching sequences between polyp and chromo labels for a given video
        """
        polyp_label_id = self.get_label_id_by_name(POLYP_LABEL_NAME)
        chromo_label_id = self.get_label_id_by_name(CHROMO_LABEL_NAME)

        # Fetch polyp and chromo sequences
        polyp_segments = LabelRawVideoSegment.objects.filter(
            label_id=polyp_label_id, video_id=video_id
        )
        chromo_segments = LabelRawVideoSegment.objects.filter(
            label_id=chromo_label_id, video_id=video_id
        )

        matching_segments = []

        # Find overlapping sequences between polyp and chromo
        for polyp in polyp_segments:
            for chromo in chromo_segments:
                if chromo.start_frame_number <= polyp.end_frame_number and chromo.end_frame_number >= polyp.start_frame_number:
                    matching_segments.append({'polyp': polyp, 'chromo': chromo})

        print(f"[DEBUG] Checking video_id={video_id}")
        print(f"[DEBUG] Polyp segments found: {polyp_segments.count()}")
        print(f"[DEBUG] Chromo segments found: {chromo_segments.count()}")
        print(f"[DEBUG] Matching segments found: {len(matching_segments)}")

        
        
        return matching_segments

    '''def apply_sequence_diversity(self, matching_segments):
        """
        Apply minimum frame gap between selected sequences to ensure diversity.
        """
        selected_sequences = []
        last_end = -float('inf')

        for match in matching_segments:
            polyp_end_frame = match['polyp'].end_frame_number
            if polyp_end_frame - last_end >= MIN_SEQUENCE_GAP_FRAMES:
                selected_sequences.append(match)
                last_end = match['polyp'].end_frame_number

        return selected_sequences'''


    def apply_sequence_diversity(self, matching_segments):
        """
        Select 3 segments that:
        - Are each at least MIN_SEGMENT_LENGTH_FRAMES long
        - Are spaced at least MIN_SEQUENCE_GAP_FRAMES apart (between end of one and start of next)
        - Out of all valid 3-segment combos, choose the one with maximum total time spacing
        - Fallback: if fewer than 3 valid, return top 3 longest
        """
        if not matching_segments:
            return []

        # Sort by time (optional for readability)
        matching_segments.sort(key=lambda seg: seg['polyp'].start_frame_number)

        # Step 1: Filter segments by minimum length
        valid_segments = [
            seg for seg in matching_segments
            if (seg['polyp'].end_frame_number - seg['polyp'].start_frame_number) >= MIN_SEGMENT_LENGTH_FRAMES
        ]

        # Step 2: Fallback if fewer than 3 valid segments exist
        if len(valid_segments) < 3:
            valid_segments.sort(
                key=lambda seg: seg['polyp'].end_frame_number - seg['polyp'].start_frame_number,
                reverse=True
            )
            return valid_segments[:3]

        # Step 3: Evaluate all 3-combinations and filter only those that meet min spacing
        best_combo = None
        best_gap_sum = -1

        for combo in combinations(valid_segments, 3):
            # Sort the combo by time
            sorted_combo = sorted(combo, key=lambda seg: seg['polyp'].start_frame_number)

            s1, s2, s3 = sorted_combo
            s1_end = s1['polyp'].end_frame_number
            s2_start = s2['polyp'].start_frame_number
            s2_end = s2['polyp'].end_frame_number
            s3_start = s3['polyp'].start_frame_number

            gap1 = s2_start - s1_end
            gap2 = s3_start - s2_end

            # Only accept combinations with minimum spacing between each segment
            if gap1 >= MIN_SEQUENCE_GAP_FRAMES and gap2 >= MIN_SEQUENCE_GAP_FRAMES:
                total_gap = gap1 + gap2

                if total_gap > best_gap_sum:
                    best_gap_sum = total_gap
                    best_combo = sorted_combo

        # Step 4: If no 3-segment combo satisfies spacing, fallback to top 3 longest
        if best_combo:
            return list(best_combo)

        valid_segments.sort(
            key=lambda seg: seg['polyp'].end_frame_number - seg['polyp'].start_frame_number,
            reverse=True
        )
        return valid_segments[:3]



    def select_frames_for_sequence(self, sequence):
        """
        Select `FRAMES_PER_SEQUENCE` frames from the sequence that:
        - Satisfy all defined rules in FRAME_SELECTION_RULES
        - Have the lowest 'low_quality' score
        - Are at least MIN_FRAME_GAP_IN_SEQUENCE apart from each other
        """
        polyp_sequence = sequence['polyp']
        video = polyp_sequence.video

        start_frame = polyp_sequence.start_frame_number
        end_frame = polyp_sequence.end_frame_number

        predictions = getattr(video, "readable_predictions", [])
        frame_dir = getattr(video, "frame_dir", "")

        if not predictions or not frame_dir:
            return []

        # Prepare all frame candidates within segment
        segment_predictions = [
            {
                "frame_number": idx,
                "low_quality": pred.get("low_quality", 1.0),
                "outside": pred.get("outside", 1.0),
                "frame_path": f"{frame_dir}/frame_{str(idx).zfill(7)}.jpg",
                "prediction": pred  # Store full prediction dict for rule checks
            }
            for idx, pred in enumerate(predictions[start_frame:end_frame + 1], start=start_frame)
            if isinstance(pred, dict)
        ]

        # Step 1: Sort by 'low_quality' to prioritize better frames
        segment_predictions.sort(key=lambda x: x["low_quality"])

        # Step 2: Apply rule-based filtering AND enforce diversity
        selected_frames = []
        last_selected_frame = -float('inf')

        for frame in segment_predictions:
            if len(selected_frames) >= FRAMES_PER_SEQUENCE:
                break

            # Check all rules
            if all(rule(frame["prediction"]) for rule in FRAME_SELECTION_RULES):
                if frame["frame_number"] - last_selected_frame >= MIN_FRAME_GAP_IN_SEQUENCE:
                    selected_frames.append({
                        "frame_number": frame["frame_number"],
                        "low_quality": frame["low_quality"],
                        "frame_path": frame["frame_path"]
                    })
                    last_selected_frame = frame["frame_number"]

        return selected_frames




    '''def select_frames_for_sequence(self, sequence):
        """
        Select `FRAMES_PER_SEQUENCE` frames from the sequence that:
        - Have the lowest 'low_quality' score
        - Are at least MIN_FRAME_GAP_IN_SEQUENCE apart from each other
        """

        polyp_sequence = sequence['polyp']
        video = polyp_sequence.video

        # Start and end frames of the segment
        start_frame = polyp_sequence.start_frame_number
        end_frame = polyp_sequence.end_frame_number

        # Get predictions list (indexed by frame number)
        predictions = getattr(video, "readable_predictions", [])
        frame_dir = getattr(video, "frame_dir", "")

        if not predictions or not frame_dir:
            return []  # Return empty if any required data is missing

        # Slice predictions to only include frames in this sequence
        segment_predictions = [
            {
                "frame_number": idx,
                "low_quality": pred.get("low_quality", 1.0),
                "frame_path": f"{frame_dir}/frame_{str(idx).zfill(7)}.jpg"
            }
            for idx, pred in enumerate(predictions[start_frame:end_frame + 1], start=start_frame)
            if isinstance(pred, dict)
        ]

        # Sort all frame candidates by lowest "low_quality"
        segment_predictions.sort(key=lambda x: x["low_quality"])

        # Pick frames with minimum spacing between them
        selected_frames = []
        last_selected_frame = -float('inf')

        for frame in segment_predictions:
            if len(selected_frames) >= FRAMES_PER_SEQUENCE:
                break

            if frame["frame_number"] - last_selected_frame >= MIN_FRAME_GAP_IN_SEQUENCE:
                selected_frames.append(frame)
                last_selected_frame = frame["frame_number"]

        return selected_frames
'''

    def to_representation(self, _):

        results = []

        #all_videos = RawVideoFile.objects.all()

        for video in self.videos:
            video_id = video.id
            matching_segments = self.get_matching_sequences(video_id)
            diverse_segments = self.apply_sequence_diversity(matching_segments)

            for segment in diverse_segments:
                frames = self.select_frames_for_sequence(segment)
                results.append({
                    "video_id": video_id,
                    "segment_start": segment['polyp'].start_frame_number,
                    "segment_end": segment['polyp'].end_frame_number,
                    "frames": frames
                })

        return results




'''
await import('https://cdn.jsdelivr.net/npm/axios/dist/axios.min.js');
const fetchNiceClassification = async () => {
    try {
        const response = await axios.get("http://localhost:8000/api/videos/nice-classification/", {
            headers: { "Accept": "application/json" }
        });

        console.log(" NICE Classification Response:", response.data);
        alert("NICE Classification data fetched!");
        return response.data;
    } catch (error) {
        console.error(" Error fetching NICE classification:", error.response?.data || error);
        alert("Failed to fetch NICE classification data.");
        return error.response?.data || { error: "Unknown error" };
    }
};

fetchNiceClassification().then(data => console.log("Final Output:", data));
'''