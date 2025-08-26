from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from endoreg_db.models import VideoFile
from ..serializers.Frames_NICE_and_PARIS_classifications import (
    ForNiceClassificationSerializer,
    ForParisClassificationSerializer
)

import logging


class ForNiceClassificationView(APIView):
    """
    NICE Classification API View
    
    GET: Führt NICE-Klassifikation für alle Videos durch
    POST: Führt NICE-Klassifikation für spezifizierte Videos durch
    
    POST Body: {"video_ids": [1, 2, 3]} oder leerer Body für alle Videos
    """

    def get(self, request):
        """Legacy GET endpoint - processes all videos"""
        print("[DEBUG] NICE Classification View hit (GET)")
        return self._process_classification(request)

    def post(self, request):
        """New POST endpoint - processes specified videos"""
        print("[DEBUG] NICE Classification View hit (POST)")
        return self._process_classification(request)

    def _process_classification(self, request):
        try:
            # Handle POST data for specific video IDs
            video_ids = None
            if request.method == 'POST' and hasattr(request, 'data'):
                video_ids = request.data.get('video_ids', None)

            if video_ids:
                videos = VideoFile.objects.filter(id__in=video_ids)
                print(f"[DEBUG] Processing NICE classification for specific videos: {video_ids}")
            else:
                videos = VideoFile.objects.all()
                print("[DEBUG] Processing NICE classification for all videos")

            print(f"[DEBUG] Total videos found: {videos.count()}")
            for v in videos:
                print(f"[DEBUG] Video ID: {v.id}")

            if not videos.exists():
                return Response(
                    {"error": "No videos found in the database."},
                    status=status.HTTP_404_NOT_FOUND
                )

            serializer = ForNiceClassificationSerializer()
            response_data = serializer.to_representation(videos)

            if not response_data:
                return Response(
                    {"error": "No valid segments for NICE classification."},
                    status=status.HTTP_404_NOT_FOUND
                )

            return Response(response_data, status=status.HTTP_200_OK)

        except Exception as e:
            return Response(
                {"error": f"Internal server error: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class ForParisClassificationView(APIView):
    """
    PARIS Classification API View
    
    GET: Führt PARIS-Klassifikation für alle Videos durch
    POST: Führt PARIS-Klassifikation für spezifizierte Videos durch
    
    POST Body: {"video_ids": [1, 2, 3]} oder leerer Body für alle Videos
    """

    def get(self, request):
        """Legacy GET endpoint - processes all videos"""
        print("[DEBUG] PARIS Classification View hit (GET)")
        return self._process_classification(request)

    def post(self, request):
        """New POST endpoint - processes specified videos"""
        print("[DEBUG] PARIS Classification View hit (POST)")
        return self._process_classification(request)

    def _process_classification(self, request):
        try:
            # Handle POST data for specific video IDs
            video_ids = None
            if request.method == 'POST' and hasattr(request, 'data'):
                video_ids = request.data.get('video_ids', None)

            if video_ids:
                videos = VideoFile.objects.filter(id__in=video_ids)
                print(f"[DEBUG] Processing PARIS classification for specific videos: {video_ids}")
            else:
                videos = VideoFile.objects.all()
                print(f"[DEBUG] Processing PARIS classification for all videos")

            print(f"[DEBUG] Total videos found: {videos.count()}")

            filtered_videos = [
                video for video in videos
                if getattr(video, "frame_dir", None)  # no more readable_predictions check
            ]

            if not filtered_videos:
                return Response(
                    {"error": "No videos with valid frame_dir found."},
                    status=status.HTTP_404_NOT_FOUND
                )

            serializer = ForParisClassificationSerializer()
            response_data = serializer.to_representation(filtered_videos)

            if not response_data:
                return Response(
                    {"error": "No valid PARIS segments found."},
                    status=status.HTTP_404_NOT_FOUND
                )

            return Response(response_data, status=status.HTTP_200_OK)

        except Exception as e:
            return Response(
                {"error": f"Internal server error: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class BatchClassificationView(APIView):
    """
    Batch Classification API View
    
    POST: Führt beide Klassifikationstypen (NICE und PARIS) für spezifizierte Videos durch
    
    POST Body: {
        "video_ids": [1, 2, 3],
        "types": ["nice", "paris"]  # Optional, default beide
    }
    """

    def post(self, request):
        try:
            video_ids = request.data.get('video_ids', None)
            classification_types = request.data.get('types', ['nice', 'paris'])

            if video_ids:
                videos = VideoFile.objects.filter(id__in=video_ids)
            else:
                videos = VideoFile.objects.all()

            if not videos.exists():
                return Response(
                    {"error": "No videos found."},
                    status=status.HTTP_404_NOT_FOUND
                )

            results = {}

            if 'nice' in classification_types:
                nice_serializer = ForNiceClassificationSerializer()
                results['nice'] = nice_serializer.to_representation(videos)

            if 'paris' in classification_types:
                # Filter videos for PARIS (need frame_dir)
                filtered_videos = [
                    video for video in videos
                    if getattr(video, "frame_dir", None)
                ]
                
                if filtered_videos:
                    paris_serializer = ForParisClassificationSerializer()
                    results['paris'] = paris_serializer.to_representation(filtered_videos)
                else:
                    results['paris'] = {"error": "No videos with valid frame_dir found for PARIS classification."}

            return Response({
                "message": "Batch classification completed.",
                "results": results
            }, status=status.HTTP_200_OK)

        except Exception as e:
            return Response(
                {"error": f"Internal server error: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class ClassificationStatusView(APIView):
    """
    Classification Status API View
    
    GET: Gibt den Status der Klassifikationen für ein Video zurück
    """

    def get(self, request, video_id):
        try:
            video = VideoFile.objects.get(id=video_id)
            
            # Check if classifications exist for this video
            # This would typically check for saved classification results in the database
            # For now, we'll return basic status information
            
            status_info = {
                "video_id": video_id,
                "video_name": getattr(video, 'original_file_name', 'Unknown'),
                "has_frame_dir": bool(getattr(video, "frame_dir", None)),
                "nice_classification_available": True,  # Always available for NICE
                "paris_classification_available": bool(getattr(video, "frame_dir", None)),
                "last_processed": None,  # Would come from classification results table
                "classification_results": {
                    "nice": None,  # Would contain saved NICE results
                    "paris": None   # Would contain saved PARIS results
                }
            }

            return Response(status_info, status=status.HTTP_200_OK)

        except VideoFile.DoesNotExist:
            return Response(
                {"error": f"Video with ID {video_id} not found."},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            return Response(
                {"error": f"Internal server error: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
