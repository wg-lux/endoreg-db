from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from ..models import RawVideoFile
from ..serializers.raw_video_meta_validation import VideoFileForMetaSerializer

class VideoFileForMetaView(APIView):
    """
    API endpoint to fetch video metadata step-by-step.
    If last_id is not provided  Returns the first video.
     If last_id is given Returns the next available video.
    """

    ##need to change this fucntion , like the previous one

    def get(self, request):
        """
        Handles:
        First video if last_id is nt in query params.
        Next video where id > last_id` if provided.
        """
        last_id = request.GET.get("last_id")  # Get last_id from query params (e.g., ?last_id=2)

        try:
            # If last_id is provided, fetch the next video where id > last_id
            # id__gt is orm syntax which is equal to SELECT * FROM rawvideofile WHERE id > 2 ORDER BY id ASC LIMIT 1;

            query_filter = {} if last_id is None else {"id__gt": int(last_id)}
            video_entry = RawVideoFile.objects.select_related("sensitive_meta").filter(**query_filter).order_by('id').first()

            if not video_entry:
                return Response({"message": "No more videos available."}, status=status.HTTP_404_NOT_FOUND)

            serializer = VideoFileForMetaSerializer(video_entry)
            return Response(serializer.data, status=status.HTTP_200_OK)

        except Exception as e:
            return Response({"error": f"Internal server error: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
