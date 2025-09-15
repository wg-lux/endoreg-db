from django.shortcuts import render, get_object_or_404
from ...models import VideoFile
import json
from ...serializers.video import VideoFileSerializer

def video_timeline_view(request, video_id):
    """
    Fetches 'outside' label segments and passes their timestamps to the template.
    """
    video_entry = get_object_or_404(VideoFile, id=video_id)

    # Get metadata (Assumes serializer method exists)
    video_serializer = VideoFileSerializer(video_entry, context={'request': request})
    label_segments = video_serializer.get_label_time_segments(video_entry)

    segments = []
    frame_markers = []  # Store frame timestamps
    fps = 50  # Fixed FPS

    # Ensure "outside" label exists
    if "outside" in label_segments:
        for segment in label_segments["outside"]["time_ranges"]:
            segments.append({
                "start_time": segment["start_time"],
                "end_time": segment["end_time"]
            })

            # Extract frame timestamps for markers
            for frame_num, _ in segment["frames"].items():
                time_in_seconds = frame_num / fps
                frame_markers.append(time_in_seconds)

    # Set video duration correctly
    video_duration = video_entry.duration if hasattr(video_entry, "duration") and video_entry.duration else 226  # Default to 226 seconds

    print("Video ID:", video_id)
    print("Segments:", segments)
    print("Video Duration:", video_duration)

    return render(request, "timeline.html", {
        "video_id": video_id,
        "video_url": video_serializer.get_video_url(video_entry),
        "segments": json.dumps(segments),  # Send segment start/end times
        "frame_markers": json.dumps(frame_markers),  # Send frame timestamps
        "video_duration": video_duration,
    })
