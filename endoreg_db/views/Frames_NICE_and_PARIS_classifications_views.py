from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from endoreg_db.models import VideoFile
from ..serializers._old.Frames_NICE_and_PARIS_classifications import (
    ForNiceClassificationSerializer,
    ForParisClassificationSerializer
)

import logging


class ForNiceClassificationView(APIView):

    def get(self, request):
        print("[DEBUG] NICE Classification View hit")

        try:
            videos = VideoFile.objects.all()
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

    def get(self, request):
        print("[DEBUG] PARIS Classification View hit")

        try:
            videos = VideoFile.objects.all()
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
