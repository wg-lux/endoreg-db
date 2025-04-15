from rest_framework import serializers
from endoreg_db.models import Label, LabelRawVideoSegment, RawVideoFile
from collections import defaultdict
import numpy as np
from itertools import combinations

# === CONFIGURABLE PARAMETERS - ForNiceClassificationSerializer ===
POLYP_LABEL_NAME = "polyp"
CHROMO_LABEL_NAME = "digital_chromo_endoscopy"
FPS = 50  # Frames per second- should fetch dynamically from rawvideofile table
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
    lambda pred: pred.get("snare", 1.0) < 0.1,

    # Add more rules easily here
]

REQUIRED_FRAME_KEYS = ["low_quality", "outside", "snare"]  # need tp update when adding rules

POLYP_CONFIDENCE_THRESHOLDS = [0.9, 0.8, 0.7] # basically this is the prediction value/score,  we are using for frame selection


class BaseClassificationSerializer(serializers.Serializer):
    LABEL_NAME = "polyp"  # default (can be overridden)
    INSTRUMENT_LABEL_NAME = "instrument"

    def get_label_id_by_name(self, label_name):
        try:
            label = Label.objects.get(name=label_name)
            return label.id
        except Label.DoesNotExist:
            raise serializers.ValidationError({
                "error": f"Label with name '{label_name}' does not exist."
            })
    def get_filtered_polyp_segments(self, video_id):
        """
        Return polyp segments that do NOT overlap with 'instrument' segments.
        """
        polyp_label_id = self.get_label_id_by_name(self.LABEL_NAME)
        instrument_label_id = self.get_label_id_by_name(self.INSTRUMENT_LABEL_NAME)

        polyp_segments = LabelRawVideoSegment.objects.filter(
            label_id=polyp_label_id, video_id=video_id
        )

        instrument_segments = LabelRawVideoSegment.objects.filter(
            label_id=instrument_label_id, video_id=video_id
        )

        def overlaps(seg1, seg2):
            return seg1.start_frame_number <= seg2.end_frame_number and seg1.end_frame_number >= seg2.start_frame_number

        filtered_polyp_segments = []
        for polyp_seg in polyp_segments:
            if not any(overlaps(polyp_seg, instr_seg) for instr_seg in instrument_segments):
                filtered_polyp_segments.append(polyp_seg)

        return filtered_polyp_segments

    def get_polyp_segments(self, video_id):
        polyp_label_id = self.get_label_id_by_name(self.LABEL_NAME)
        return LabelRawVideoSegment.objects.filter(label_id=polyp_label_id, video_id=video_id)

    def apply_sequence_diversity(self, matching_segments):
        if not matching_segments:
            return []

        matching_segments.sort(key=lambda seg: seg['polyp'].start_frame_number)

        valid_segments = [
            seg for seg in matching_segments
            if (seg['polyp'].end_frame_number - seg['polyp'].start_frame_number) >= MIN_SEGMENT_LENGTH_FRAMES
        ]

        if len(valid_segments) < 3:
            valid_segments.sort(
                key=lambda seg: seg['polyp'].end_frame_number - seg['polyp'].start_frame_number,
                reverse=True
            )
            return valid_segments[:3]

        best_combo = None
        best_gap_sum = -1

        for combo in combinations(valid_segments, 3):
            sorted_combo = sorted(combo, key=lambda seg: seg['polyp'].start_frame_number)
            s1, s2, s3 = sorted_combo
            s1_end = s1['polyp'].end_frame_number
            s2_start = s2['polyp'].start_frame_number
            s2_end = s2['polyp'].end_frame_number
            s3_start = s3['polyp'].start_frame_number
            gap1 = s2_start - s1_end
            gap2 = s3_start - s2_end

            if gap1 >= MIN_SEQUENCE_GAP_FRAMES and gap2 >= MIN_SEQUENCE_GAP_FRAMES:
                total_gap = gap1 + gap2
                if total_gap > best_gap_sum:
                    best_gap_sum = total_gap
                    best_combo = sorted_combo

        if best_combo:
            return list(best_combo)

        valid_segments.sort(
            key=lambda seg: seg['polyp'].end_frame_number - seg['polyp'].start_frame_number,
            reverse=True
        )
        return valid_segments[:3]
    
    # # If any error occurs in this function, use the commented one below(fallback:select_frames_for_sequence).
    def select_frames_for_sequence(self, sequence):
        # Extract the polyp segment from the current sequence
        polyp_sequence = sequence['polyp']
        video = polyp_sequence.video

        # Get the start and end frame numbers of the segment/sequence
        start_frame = polyp_sequence.start_frame_number
        end_frame = polyp_sequence.end_frame_number

        # here we are getting the predictions and frame directory from the video object
        frame_dir = getattr(video, "frame_dir", "") #need to check

        # If either predictions or frame_dir is missing, return an empty list
        if not predictions or not frame_dir:
            return []

        # Build a list of prediction dictionaries for each frame in the segment
        # Each entry includes frame number, selected prediction keys, path, and full prediction
        segment_predictions = [
            {
                "frame_number": idx,
                **{key: pred.get(key, 1.0) for key in REQUIRED_FRAME_KEYS + ["polyp"]},
                "frame_path": f"{frame_dir}/frame_{str(idx).zfill(7)}.jpg",
                "prediction": pred
            }
            for idx, pred in enumerate(predictions[start_frame:end_frame + 1], start=start_frame)
            if isinstance(pred, dict)
        ]

        # Sort the frames by lowest 'low_quality' value first,# for higher quality images// changeable, 
        segment_predictions.sort(key=lambda x: x["low_quality"])

        # Try frame selection with decreasing polyp confidence thresholds
        #for polyp_threshold in [0.9, 0.8, 0.7]:
        for polyp_threshold in POLYP_CONFIDENCE_THRESHOLDS:

            # Filter frames that meet the current polyp threshold
            candidate_frames = [
                frame for frame in segment_predictions
                if frame["prediction"].get("polyp", 0.0) > polyp_threshold
            ]

            # If no candidates at this threshold, try the next lower threshold
            if not candidate_frames:
                continue

            # Initialize list of selected frames and track last selected frame number
            selected_frames = []
            last_selected_frame = -float('inf')

            # Iterate through the candidate frames
            for frame in candidate_frames:
                # Stop if we've already selected enough frames
                if len(selected_frames) >= FRAMES_PER_SEQUENCE:
                    break

                # Apply the standard filtering rules and spacing condition
                if (
                    frame["prediction"].get("low_quality", 1.0) < 0.1 and
                    frame["prediction"].get("outside", 1.0) < 0.1 and
                    frame["prediction"].get("snare", 1.0) < 0.1 and
                    frame["frame_number"] - last_selected_frame >= MIN_FRAME_GAP_IN_SEQUENCE
                ):
                    # Frame passes all filters and spacing rule, so select it
                    selected_frames.append({
                        "frame_number": frame["frame_number"],
                        "low_quality": frame["low_quality"],
                        "polyp_score": frame["prediction"]["polyp"],
                        "frame_path": frame["frame_path"]
                    })
                    last_selected_frame = frame["frame_number"]

            # If we found enough valid frames at this threshold, return them
            if len(selected_frames) >= FRAMES_PER_SEQUENCE:
                return selected_frames

        # If no threshold yielded enough frames, return what was found in the last attempt (could be empty)
        return selected_frames if selected_frames else []

    # fallback:select_frames_for_sequence
    '''def select_frames_for_sequence(self, sequence):
        print("----------------------in selected_frames fro sequnces funtion ----------------------------------------")
        polyp_sequence = sequence['polyp']
        video = polyp_sequence.video
        start_frame = polyp_sequence.start_frame_number
        end_frame = polyp_sequence.end_frame_number
        predictions = getattr(video, "readable_predictions", [])
        frame_dir = getattr(video, "frame_dir", "")

        if not predictions or not frame_dir:
            return []

        segment_predictions = [
            {
                "frame_number": idx,
                **{key: pred.get(key, 1.0) for key in REQUIRED_FRAME_KEYS},
                "frame_path": f"{frame_dir}/frame_{str(idx).zfill(7)}.jpg",
                "prediction": pred
            }
            for idx, pred in enumerate(predictions[start_frame:end_frame + 1], start=start_frame)
            if isinstance(pred, dict)
        ]


        segment_predictions.sort(key=lambda x: x["low_quality"])

        selected_frames = []
        last_selected_frame = -float('inf')

        for frame in segment_predictions:
            if len(selected_frames) >= FRAMES_PER_SEQUENCE:
                break

            if all(rule(frame["prediction"]) for rule in FRAME_SELECTION_RULES):
                if frame["frame_number"] - last_selected_frame >= MIN_FRAME_GAP_IN_SEQUENCE:
                    selected_frames.append({
                        "frame_number": frame["frame_number"],
                        "low_quality": frame["low_quality"],
                        "frame_path": frame["frame_path"]
                    })
                    last_selected_frame = frame["frame_number"]

        return selected_frames'''
class ForNiceClassificationSerializer(BaseClassificationSerializer):
    
    def get_matching_sequences(self, video_id):
        polyp_segments = self.get_filtered_polyp_segments(video_id)
        chromo_label_id = self.get_label_id_by_name(CHROMO_LABEL_NAME)

        chromo_segments = LabelRawVideoSegment.objects.filter(
            label_id=chromo_label_id, video_id=video_id
        )

        matching_segments = []
        for polyp in polyp_segments:
            for chromo in chromo_segments:
                if chromo.start_frame_number <= polyp.end_frame_number and chromo.end_frame_number >= polyp.start_frame_number:
                    matching_segments.append({'polyp': polyp, 'chromo': chromo})

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
    '''def to_representation(self, videos):
        results = []

        for video in videos:
            print(f"serializers ::: video ID: {video.id}")
            try:
                video_id = video.id
                matching_segments = self.get_matching_sequences(video_id)
                print(f"serializers ::: matching_segments: {len(matching_segments)}")
                diverse_segments = self.apply_sequence_diversity(matching_segments)
                print(f"serializers ::: diverse_segments: {len(diverse_segments)}")

                for segment in diverse_segments:
                    frames = self.select_frames_for_sequence(segment)
                    print(f"serializers ::: selected {len(frames)} frames for segment {segment['polyp'].start_frame_number}–{segment['polyp'].end_frame_number}")
                    results.append({
                        "video_id": video_id,
                        "segment_start": segment['polyp'].start_frame_number,
                        "segment_end": segment['polyp'].end_frame_number,
                        "frames": frames
                    })

            except Exception as e:
                print(f" ERROR while processing video ID {video.id}: {e}")

        print("serializers ::: returning results")
        return results'''
    
    def to_representation(self, videos):
        results = []

        for video in videos:
            print(f"serializers ::: video ID: {video.id}")
            try:
                video_id = video.id

                matching_segments = self.get_matching_sequences(video_id)
                print(f"serializers ::: matching_segments: {len(matching_segments)}")

                if not matching_segments:
                    results.append({
                        "video_id": video_id,
                        "message": "No matching NICE segments found — possibly filtered due to overlap with 'instrument' label or no chromo overlap."
                    })
                    continue

                diverse_segments = self.apply_sequence_diversity(matching_segments)
                print(f"serializers ::: diverse_segments: {len(diverse_segments)}")

                if not diverse_segments:
                    results.append({
                        "video_id": video_id,
                        "message": "No diverse NICE sequences found — either too short or not spaced enough apart."
                    })
                    continue

                for segment in diverse_segments:
                    frames = self.select_frames_for_sequence(segment)
                    print(f"serializers ::: selected {len(frames)} frames for segment {segment['polyp'].start_frame_number}–{segment['polyp'].end_frame_number}")

                    if not frames:
                        results.append({
                            "video_id": video_id,
                            "segment_start": segment['polyp'].start_frame_number,
                            "segment_end": segment['polyp'].end_frame_number,
                            "message": "No valid frames passed filtering rules (low_quality, outside, snare)."
                        })
                    else:
                        results.append({
                            "video_id": video_id,
                            "segment_start": segment['polyp'].start_frame_number,
                            "segment_end": segment['polyp'].end_frame_number,
                            "frames": frames
                        })

            except Exception as e:
                print(f" ERROR while processing video ID {video.id}: {e}")
                results.append({
                    "video_id": video.id,
                    "error": str(e)
                })

        if not results:
            return [{
                "message": "No classification data generated for any videos."
            }]

        print("serializers ::: returning results")
        return results

    

class ForParisClassificationSerializer(BaseClassificationSerializer):
   
    def get_filtered_polyp_segments(self, video_id):
        """
        Override to exclude polyp segments that overlap with 'instrument' or 'digital_chromo_endoscopy'
        """
        polyp_label_id = self.get_label_id_by_name(self.LABEL_NAME)
        instrument_label_id = self.get_label_id_by_name(self.INSTRUMENT_LABEL_NAME)
        chromo_label_id = self.get_label_id_by_name(CHROMO_LABEL_NAME)

        polyp_segments = LabelRawVideoSegment.objects.filter(
            label_id=polyp_label_id, video_id=video_id
        )

        instrument_segments = LabelRawVideoSegment.objects.filter(
            label_id=instrument_label_id, video_id=video_id
        )

        chromo_segments = LabelRawVideoSegment.objects.filter(
            label_id=chromo_label_id, video_id=video_id
        )

        def overlaps(seg1, seg2):
            return seg1.start_frame_number <= seg2.end_frame_number and seg1.end_frame_number >= seg2.start_frame_number

        filtered_segments = []
        for polyp_seg in polyp_segments:
            if not any(overlaps(polyp_seg, instr_seg) for instr_seg in instrument_segments) and \
               not any(overlaps(polyp_seg, chromo_seg) for chromo_seg in chromo_segments):
                filtered_segments.append(polyp_seg)

        return filtered_segments

    def get_matching_sequences(self, video_id):
        segments = self.get_filtered_polyp_segments(video_id)
        return [{'polyp': seg} for seg in segments]





    '''def get_matching_sequences(self, video_id):
        segments = self.get_polyp_segments(video_id)
        return [{'polyp': seg} for seg in segments]'''
    ''' def get_matching_sequences(self, video_id):
        segments = self.get_filtered_polyp_segments(video_id)
        return [{'polyp': seg} for seg in segments]'''
    
    def to_representation(self, videos):
        results = []
        
        for video in videos:
            try:
                video_id = video.id

                matching_segments = self.get_matching_sequences(video_id)
                if not matching_segments:
                    results.append({
                        "video_id": video_id,
                        "message": "No matching polyp segments found — possibly filtered due to overlap with 'instrument' label."
                    })
                    continue

                diverse_segments = self.apply_sequence_diversity(matching_segments)
                if not diverse_segments:
                    results.append({
                        "video_id": video_id,
                        "message": "No diverse sequences found — either too short or too close to each other."
                    })
                    continue

                for segment in diverse_segments:
                    frames = self.select_frames_for_sequence(segment)
                    if not frames:
                        results.append({
                            "video_id": video_id,
                            "segment_start": segment['polyp'].start_frame_number,
                            "segment_end": segment['polyp'].end_frame_number,
                            "message": "No valid frames selected due to prediction rules (e.g., quality, outside, snare)."
                        })
                    else:
                        results.append({
                            "video_id": video_id,
                            "segment_start": segment['polyp'].start_frame_number,
                            "segment_end": segment['polyp'].end_frame_number,
                            "frames": frames
                        })

            except Exception as e:
                results.append({
                    "video_id": video.id,
                    "error": str(e)
                })

        if not results:
            return [{
                "message": "No valid classification results could be generated for any video."
            }]

        return results


    '''def to_representation(self, videos):
        results = []
        for video in videos:
            try:
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
            except Exception as e:
                print(f"Error processing PARIS for video {video.id}: {e}")
        return results
'''







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