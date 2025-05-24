from endoreg_db.utils.extract_specific_frames import extract_selected_frames
from rest_framework import serializers
from endoreg_db.models import Label, LabelVideoSegment
from itertools import combinations
from pathlib import Path
from django.conf import settings
# === CONFIGURABLE PARAMETERS - ForNiceClassificationSerializer ===
POLYP_LABEL_NAME = "polyp"
CHROMO_LABEL_NAMES = ["digital_chromo_endoscopy", "nbi"]
FPS = 50  # Frames per second- should fetch dynamically from videofile table
# Sequence-level filtering
MIN_SEQUENCE_GAP_SECONDS = 0.5 # Enforce diversity between sequences
MIN_SEQUENCE_GAP_FRAMES = FPS * MIN_SEQUENCE_GAP_SECONDS  # Convert to frame count
# Minimum length of a segment in seconds
MIN_SEGMENT_LENGTH_SECONDS = 0.5
MIN_SEGMENT_LENGTH_FRAMES = FPS * MIN_SEGMENT_LENGTH_SECONDS

# Frame-level selection within a sequence
MIN_FRAME_GAP_SECONDS = 1  # Minimum gap between selected frames in seconds
MIN_FRAME_GAP_IN_SEQUENCE = FPS * MIN_FRAME_GAP_SECONDS  # Convert to frame count
FRAMES_PER_SEQUENCE = 3  # Number of frames to select per matched sequence


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
INSTRUMENT_LABEL_NAMES = ["instrument", "snare", "needle"]  # Add more as needed later


class BaseClassificationSerializer(serializers.Serializer):
    """
    Base class for NICE and PARIS serializers.
    Handles label lookup, chromo/instrument segment filtering, and shared utilities.
    """
    #TODO add create method
    #TODO add update method
    LABEL_NAME = "polyp"  # default (can be overridden)
    INSTRUMENT_LABEL_NAME = "instrument" #TODO @Hamzaukw we should define frequently used labels in a utils file
    

    def get_label_id_by_name(self, label_name):
        """
        Get the ID of a label by its name.
        Raises a validation error if not found.
        """
        try:
            label = Label.objects.get(name=label_name)
            return label.id
        except Label.DoesNotExist:
            raise serializers.ValidationError({
                "error": f"Label with name '{label_name}' does not exist."
            })
        
    def get_label_ids_by_names(self, label_names):
        """
        Get a list of label IDs for a given list of label names.
        Ensures all names exist in the database.
        """
        labels = Label.objects.filter(name__in=label_names)
        label_map = {label.name: label.id for label in labels}

        missing = set(label_names) - set(label_map.keys())
        if missing:
            raise serializers.ValidationError({
                "error": f"Labels not found: {', '.join(missing)}"
            })

        return list(label_map.values())
    
    def get_chromo_segments(self, video_id):
        """
        Fetch all segments that match chromo-like labels (e.g., chromo or NBI).
        Used in both NICE and PARIS, but interpreted differently:
        - NICE: chromo overlap is required
        - PARIS: chromo overlap is disqualifying
        """
        chromo_label_ids = self.get_label_ids_by_names(CHROMO_LABEL_NAMES)
        return LabelVideoSegment.objects.filter(label_id__in=chromo_label_ids, video_file_id=video_id)


        
    def get_filtered_polyp_segments(self, video_id):
        """
        Return polyp segments that do NOT overlap with 'instrument' segments.
        """
        polyp_label_id = self.get_label_id_by_name(self.LABEL_NAME)
        instrument_label_id = self.get_label_id_by_name(self.INSTRUMENT_LABEL_NAME)

        print("polyp label id is :-", polyp_label_id,"and - instrument_label_id is ,", instrument_label_id)

        polyp_segments = LabelVideoSegment.objects.filter(
            label_id=polyp_label_id, video_file_id=video_id
        )

        instrument_segments = LabelVideoSegment.objects.filter(
            label_id=instrument_label_id, video_file_id=video_id
        )
        print("------------------------ --------------------------------- -------------------------------")
        print("polyp label id is :-", polyp_label_id,"and - instrument_label_id is ,", instrument_label_id)
        print("polyp segments are",polyp_segments , "instrument_segments are", instrument_segments)

        def overlaps(seg1, seg2):
            return seg1.start_frame_number <= seg2.end_frame_number and seg1.end_frame_number >= seg2.start_frame_number

        filtered_polyp_segments = []
        for polyp_seg in polyp_segments:
            if not any(overlaps(polyp_seg, instr_seg) for instr_seg in instrument_segments):
                filtered_polyp_segments.append(polyp_seg)

        return filtered_polyp_segments

    def get_polyp_segments(self, video_id):
        polyp_label_id = self.get_label_id_by_name(self.LABEL_NAME)
        return LabelVideoSegment.objects.filter(label_id=polyp_label_id, video_file_id=video_id)

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
    
    def select_frames_for_sequence(self, sequence):
        """
        Selects evenly spaced representative frames from a polyp segment.
        
        Frames are chosen from the segment's frame range, ensuring a minimum gap between selected frames. Returns a list of dictionaries containing frame numbers and their corresponding file paths. If the video's frame directory is unavailable, returns an empty list.
        """
        polyp_sequence = sequence['polyp']
        video = polyp_sequence.video_file

        start_frame = polyp_sequence.start_frame_number
        end_frame = polyp_sequence.end_frame_number

        frame_dir = getattr(video, "frame_dir", "")

        if not frame_dir:
            return []

        # Just create frames assuming predictions aren't available
        segment_frames = [
            {
                "frame_number": idx,
                "frame_path": f"{frame_dir}/frame_{str(idx).zfill(7)}.png"
            }
            for idx in range(start_frame, end_frame + 1)
        ]

        selected_frames = []
        last_selected_frame = -float('inf')

        for frame in segment_frames:
            if len(selected_frames) >= FRAMES_PER_SEQUENCE:
                break

            if frame["frame_number"] - last_selected_frame >= MIN_FRAME_GAP_IN_SEQUENCE:
                selected_frames.append({
                    "frame_number": frame["frame_number"],
                    "frame_path": frame["frame_path"]
                })
                last_selected_frame = frame["frame_number"]

        return selected_frames
    
    def extract_and_save_selected_frames(self, video, frame_numbers, classification_type: str):
        """
        Extract specific frames from the original video file and save them
        into a structured folder path based on the classification type.

        Args:
            video (VideoFile): Video object with `original_file_name` and `frame_dir`.
            frame_numbers (List[int]): List of frame numbers to extract.
            classification_type (str): Either "nice" or "paris".
        """
        # Resolve the path to the original video
        original_path = Path(video.original_file_name)
        if not original_path.is_absolute():
            base_video_dir = settings.BASE_DIR.parent.parent / "production_test" / "endoreg-db" / "data" / "coloreg_first_test_batch"            
            original_path = base_video_dir / original_path

        # Define output directory based on classification and video ID
        output_path = Path(video.frame_dir) / classification_type / f"video_{video.id}"

        # Extract frames using the shared utility
        extract_selected_frames(
            video_path=original_path,
            frame_numbers=frame_numbers,
            output_dir=output_path,
            fps=FPS
        )

    
    '''    # # If any error occurs in this function, use the commented one below(fallback:select_frames_for_sequence).
    def select_frames_for_sequence(self, sequence):
        # Extract the polyp segment from the current sequence
        polyp_sequence = sequence['polyp']
        video = polyp_sequence.video

        # Get the start and end frame numbers of the segment/sequence
        start_frame = polyp_sequence.start_frame_number
        end_frame = polyp_sequence.end_frame_number

        # here we aregetting the predictions and frame directory from the video object
        predictions = getattr(video, "readable_predictions", [])
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
        return selected_frames if selected_frames else []'''


    # fallback:select_frames_for_sequence
    '''
    def select_frames_for_sequence(self, sequence):
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
    """
    NICE classification logic:
    - Requires polyp segments
    - Filters out any overlap with instrument labels
    - Requires overlap with chromo-like labels (chromo/NBI)
    - Selects diverse sequences and representative frames from each
    """
    def get_matching_sequences(self, video_id):
        """
        1. Fetch all polyp segments in a video
        2. Remove parts overlapping with any instrument-type labels
        3. Keep only parts that still overlap with chromo/NBI
        4. Trim segments accordingly and return valid subsegments
        """
        polyp_label_id = self.get_label_id_by_name(self.LABEL_NAME)
        instrument_label_ids = self.get_label_ids_by_names(INSTRUMENT_LABEL_NAMES)
        #chromo_label_id = self.get_label_id_by_name(CHROMO_LABEL_NAME)
        chromo_segments = self.get_chromo_segments(video_id)


        polyp_segments = LabelVideoSegment.objects.filter(label_id=polyp_label_id, video_file_id=video_id)
        instrument_segments = LabelVideoSegment.objects.filter(label_id__in=instrument_label_ids, video_file_id=video_id)        
        
        #chromo_segments = LabelVideoSegment.objects.filter(label_id=chromo_label_id, video_file_id=video_id)
        print("polyp_label_id is", polyp_label_id, "and ployp_segment is ", polyp_segments)
        print("instrument_segments is", instrument_segments)
        #print("chromo_segments is ",chromo_segments)

        def overlaps(seg1, seg2):
            return seg1.start_frame_number <= seg2.end_frame_number and seg1.end_frame_number >= seg2.start_frame_number

        def subtract_overlap(seg, overlaps):
            start = seg.start_frame_number
            end = seg.end_frame_number
            blocks = [(o.start_frame_number, o.end_frame_number) for o in overlaps]
            blocks.sort()

            safe_ranges = []
            curr = start

            for o_start, o_end in blocks:
                if o_end < curr:
                    continue
                if o_start > end:
                    break
                if o_start > curr:
                    safe_ranges.append((curr, min(o_start - 1, end)))
                curr = max(curr, o_end + 1)

            if curr <= end:
                safe_ranges.append((curr, end))

            return safe_ranges

        matching_segments = []

        for polyp in polyp_segments:
            overlapping_instr = [seg for seg in instrument_segments if overlaps(polyp, seg)]
            safe_ranges = subtract_overlap(polyp, overlapping_instr)

            for start, end in safe_ranges:
                if end - start + 1 < MIN_SEGMENT_LENGTH_FRAMES:
                    continue

                for chromo in chromo_segments:
                    if chromo.start_frame_number <= end and chromo.end_frame_number >= start:
                        overlap_start = max(start, chromo.start_frame_number)
                        overlap_end = min(end, chromo.end_frame_number)

                        if overlap_end - overlap_start + 1 >= MIN_SEGMENT_LENGTH_FRAMES:
                            overlapping_segment = LabelVideoSegment(
                                video_file=polyp.video_file,
                                start_frame_number=overlap_start,
                                end_frame_number=overlap_end
                            )
                            matching_segments.append({'polyp': overlapping_segment})

        return matching_segments

    def to_representation(self, videos):
        """
        Processes a list of videos:
        - Applies NICE/PARIS rules to find segments
        - Applies sequence diversity logic
        - Selects frames per segment
        - Extracts all frames for a video in a single call
        - Returns structured output with messages per video
        """
        results = []

        for video in videos:
            try:
                video_id = video.id
                print("video-id is", video_id)

                matching_segments = self.get_matching_sequences(video_id)
                print("matching_segments are", matching_segments)
                if not matching_segments:
                    results.append({
                        "video_id": video_id,
                        "message": "No valid polyp segments overlapping with chromo and not overlapping with instrument."
                    })
                    continue

                diverse_segments = self.apply_sequence_diversity(matching_segments)
                if not diverse_segments:
                    results.append({
                        "video_id": video_id,
                        "message": "No diverse NICE sequences found — either too short or too close together."
                    })
                    continue

                # Collect all frame numbers to extract only once per video
                all_frames = []
                segment_results = []

                for segment in diverse_segments:
                    frames = self.select_frames_for_sequence(segment)

                    if not frames:
                        segment_results.append({
                            "video_id": video_id,
                            "segment_start": segment['polyp'].start_frame_number,
                            "segment_end": segment['polyp'].end_frame_number,
                            "message": "No valid frames passed filtering rules (low_quality, outside, snare)."
                        })
                    else:
                        segment_results.append({
                            "video_id": video_id,
                            "segment_start": segment['polyp'].start_frame_number,
                            "segment_end": segment['polyp'].end_frame_number,
                            "frames": frames
                        })
                        all_frames.extend(frames)

                # Extract once per video
                if all_frames:
                    unique_frame_numbers = sorted({f["frame_number"] for f in all_frames})
                    classification_type = "nice" if isinstance(self, ForNiceClassificationSerializer) else "paris"
                    self.extract_and_save_selected_frames(video, unique_frame_numbers, classification_type)

                results.extend(segment_results)

            except Exception as e:
                results.append({
                    "video_id": video.id,
                    "error": str(e)
                })

        if not results:
            return [{
                "message": "No classification data generated for any videos."
            }]

        return {
            "message": "NICE classification data generated." if isinstance(self, ForNiceClassificationSerializer)
            else "PARIS classification data generated.",
            "data": results
        }

    

    

class ForParisClassificationSerializer(BaseClassificationSerializer):
    """
    PARIS classification logic:
    - Requires polyp segments
    - Excludes any region overlapping with instruments or chromo/NBI
    - Keeps remaining 'clean' subsegments
    - Enforces minimum length and sequence diversity
    - Selects frames from each valid segment
    """
   
    def get_filtered_polyp_segments(self, video_id):
        """
        1. Fetch all polyp segments
        2. Subtract any overlap with chromo or instrument segments
        3. Keep only trimmed segments longer than the minimum threshold
        """
   
        polyp_label_id = self.get_label_id_by_name(self.LABEL_NAME)
        instrument_label_ids = self.get_label_ids_by_names(INSTRUMENT_LABEL_NAMES)        
        #chromo_label_id = self.get_label_id_by_name(CHROMO_LABEL_NAME)

        polyp_segments = LabelVideoSegment.objects.filter(label_id=polyp_label_id, video_file_id=video_id)
        instrument_segments = LabelVideoSegment.objects.filter(label_id__in=instrument_label_ids, video_file_id=video_id)
        #chromo_segments = LabelVideoSegment.objects.filter(label_id=chromo_label_id, video_file_id=video_id)
        chromo_segments = self.get_chromo_segments(video_id)


        def overlaps(seg1, seg2):
            return seg1.start_frame_number <= seg2.end_frame_number and seg1.end_frame_number >= seg2.start_frame_number

        def subtract_overlap(seg, overlaps):
            """
            Given a base segment and list of overlapping segments,
            returns list of non-overlapping sub-segments.
            """
            start = seg.start_frame_number
            end = seg.end_frame_number
            blocks = [(o.start_frame_number, o.end_frame_number) for o in overlaps]
            blocks.sort()

            safe_ranges = []
            curr = start

            for o_start, o_end in blocks:
                if o_end < curr:
                    continue
                if o_start > end:
                    break
                if o_start > curr:
                    safe_ranges.append((curr, min(o_start - 1, end)))
                curr = max(curr, o_end + 1)

            if curr <= end:
                safe_ranges.append((curr, end))

            return safe_ranges

        # Final valid sub-segments
        trimmed_segments = []

        for polyp_seg in polyp_segments:
            # Collect all overlapping regions with instrument or chromo
            overlapping = [
                seg for seg in (list(instrument_segments) + list(chromo_segments))
                if overlaps(polyp_seg, seg)
            ]

            safe_ranges = subtract_overlap(polyp_seg, overlapping)

            for start, end in safe_ranges:
                if end - start + 1 >= MIN_SEGMENT_LENGTH_FRAMES:
                    trimmed_segments.append(LabelVideoSegment(
                        video_file=polyp_seg.video_file,
                        start_frame_number=start,
                        end_frame_number=end
                    ))

            return trimmed_segments

        filtered_segments = []
        for polyp_seg in polyp_segments:
            if not any(overlaps(polyp_seg, instr_seg) for instr_seg in instrument_segments) and \
               not any(overlaps(polyp_seg, chromo_seg) for chromo_seg in chromo_segments):
                filtered_segments.append(polyp_seg)

        return filtered_segments

    def get_matching_sequences(self, video_id):
        """
        Wraps all valid polyp subsegments into a uniform data structure.
        This is needed for consistent downstream processing.
        """
        segments = self.get_filtered_polyp_segments(video_id)
        return [{'polyp': seg} for seg in segments]

    
    def to_representation(self, videos):
        """
        Processes videos for PARIS classification.
        Filters segments, selects diverse sequences, and picks valid frames.
        Extracts all selected frames once per video.
        Returns detailed per-video feedback or results.
        """
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

                all_frames = []
                segment_results = []

                for segment in diverse_segments:
                    frames = self.select_frames_for_sequence(segment)

                    if not frames:
                        segment_results.append({
                            "video_id": video_id,
                            "segment_start": segment['polyp'].start_frame_number,
                            "segment_end": segment['polyp'].end_frame_number,
                            "message": "No valid frames selected due to prediction rules (e.g., quality, outside, snare)."
                        })
                    else:
                        segment_results.append({
                            "video_id": video_id,
                            "segment_start": segment['polyp'].start_frame_number,
                            "segment_end": segment['polyp'].end_frame_number,
                            "frames": frames
                        })
                        all_frames.extend(frames)

                if all_frames:
                    unique_frame_numbers = sorted({f["frame_number"] for f in all_frames})
                    self.extract_and_save_selected_frames(video, unique_frame_numbers, classification_type="paris")

                results.extend(segment_results)

            except Exception as e:
                results.append({
                    "video_id": video.id,
                    "error": str(e)
                })

        if not results:
            return [{
                "message": "No valid classification results could be generated for any video."
            }]

        return {
            "message": "PARIS classification data generated.",
            "data": results
        }



"""
NICE Classification Serializer
------------------------------

This serializer identifies clean polyp segments for NICE classification:
- Filters out segments overlapping with instruments
- Requires overlap with digital chromo or NBI
- Applies sequence diversity and selects clean frames

Configurable via:
- INSTRUMENT_LABEL_NAMES: defines what tools to exclude
- CHROMO_LABEL_NAMES: defines what chromo types to include

Returns:
    {
        "video_id": int,
        "segment_start": int,
        "segment_end": int,
        "frames": [{ frame_number, frame_path }]
    }
"""


"""
PARIS Classification Serializer
-------------------------------

This serializer identifies clean polyp segments for PARIS classification:
- Filters out segments that overlap with any instrument-type labels
- Also excludes any segments that overlap with digital chromo or NBI imaging
- Trims overlapping regions and returns only clean, usable subsegments
- Applies sequence diversity to avoid redundant or closely positioned segments
- Selects clean, representative frames from each remaining valid segment

Configurable via:
- INSTRUMENT_LABEL_NAMES: defines all instrument labels that must be excluded (e.g., 'instrument', 'snare', 'needle')
- CHROMO_LABEL_NAMES: defines chromo-like labels that should also be excluded (e.g., 'digital_chromo_endoscopy', 'nbi')

Returns:
    {
        "video_id": int,
        "segment_start": int,
        "segment_end": int,
        "frames": [{ frame_number: int, frame_path: str }]
    }
"""





'''
await import('https://cdn.jsdelivr.net/npm/axios/dist/axios.min.js');
const fetchNiceClassification = async () => {
    try {
        const response = await axios.get("http://localhost:8000/videos/nice-classification/", {
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