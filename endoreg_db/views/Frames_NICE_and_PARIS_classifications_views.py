from urllib import response
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from ..models import VideoFile
#from ..serializers.Frames_NICE_and_PARIS_classifications import ForNiceClassificationSerializer
from ..serializers._old.Frames_NICE_and_PARIS_classifications import ForNiceClassificationSerializer

import logging


class ForNiceClassificationView(APIView):

   
    def get(self, request):
        print(" [DEBUG] NICE Classification View hit")
        
        try:
            videos = VideoFile.objects.all()
            print(f"[DEBUG] Total videos found in RawVideoFile: {videos.count()}")
            for v in videos:
                print(f"[DEBUG] Video ID: {v.id}, Frame Dir: {getattr(v, 'frame_dir', None)}, "
                    f"Predictions Type: {type(getattr(v, 'readable_predictions', None))}, "
                    f"Predictions Length: {len(getattr(v, 'readable_predictions', []))}")


            if not videos.exists():
                return Response(
                    {"error": "No videos found in the database."},
                    status=status.HTTP_404_NOT_FOUND
                )

            #  Filter out videos without required fields
            filtered_videos = [
                video for video in videos
                if getattr(video, "readable_predictions", None)
                and getattr(video, "frame_dir", None)
            ]

            if not filtered_videos:
                return Response(
                    {"error": "No valid videos found with predictions and frame directory."},
                    status=status.HTTP_404_NOT_FOUND
                )

            #  Use a single serializer call for all filtered videos - NEED TO CHECK:it should automatically call to_representation function
            #serializer = ForNiceClassificationSerializer(filtered_videos)
            #serializer.is_valid()  # This won't do anything, but required for .data to be populated
            #response_data = serializer.data

            print("serializers ::: -- START")
            # serializer = ForNiceClassificationSerializer()
            # response_data = serializer.to_representation(filtered_videos)
            response_data = ""
            print("serializers response data::: --", response_data)

          
            if not response_data:
                return Response(
                    {"error": "No valid segments - found for NICE classification."},
                    status=status.HTTP_404_NOT_FOUND
                )

            return Response(response_data, status=status.HTTP_200_OK)

        except Exception as e:
            return Response(
                {"error": f"Internal server error: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

# from ..serializers.Frames_NICE_and_PARIS_classifications import ForParisClassificationSerializer

class ForParisClassificationView(APIView):
    def get(self, request):
        print(" [DEBUG] PARIS Classification View hit")

        try:
            videos = VideoFile.objects.all()
            filtered_videos = [
                video for video in videos
                if getattr(video, "readable_predictions", None)
                and getattr(video, "frame_dir", None)
            ]

            if not filtered_videos:
                return Response({"error": "No valid videos found."}, status=status.HTTP_404_NOT_FOUND)

            # serializer = ForParisClassificationSerializer()
            response_data = "" # serializer.to_representation(filtered_videos)

            if not response_data:
                return Response({"error": "No valid PARIS segments found."}, status=status.HTTP_404_NOT_FOUND)

            return Response(response_data, status=status.HTTP_200_OK)

        except Exception as e:
            return Response({"error": f"Internal server error: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


''''
from django.urls import path
from .views.Frames_NICE_and_PARIS_classifications_views import ForNiceClassificationView

urlpatterns = [
    path('api/video/<int:video_id>/nice-classification/', ForNiceClassificationView.as_view(), name='nice-classification'),
]

'''