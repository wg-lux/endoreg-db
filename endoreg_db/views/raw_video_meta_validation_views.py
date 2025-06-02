from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.http import FileResponse, Http404
import mimetypes
import os
from ..serializers._old.raw_video_meta_validation import VideoFileForMetaSerializer,SensitiveMetaUpdateSerializer
from endoreg_db.models import SensitiveMeta


class VideoFileForMetaView(APIView):
    """
    API endpoint to fetch video metadata step-by-step.
    Uses the serializer to get the first or next available video.
    
    - Returns JSON metadata when Accept: application/json
    - Returns video file when accessed directly by video tag
    """

    def get(self, request):
        """
        - Returns video file stream if Accept header is not application/json
        - Returns JSON metadata if Accept header is application/json
        - Fetches the **first available video** if `last_id` is NOT provided.
        - Fetches the **next available video** where `id > last_id` if provided.
        """
        # Get video ID from URL path if present
        video_id = None
        if hasattr(request, 'resolver_match') and request.resolver_match.kwargs:
            video_id = request.resolver_match.kwargs.get('video_id')
        
        # Check Accept header to determine response type
        accept_header = request.META.get('HTTP_ACCEPT', '')
        
        # If video_id is provided and Accept header doesn't specifically request JSON,
        # serve the video file directly
        if video_id and 'application/json' not in accept_header:
            try:
                from ..models import VideoFile
                video_entry = VideoFile.objects.get(id=video_id)
                return self.serve_video_file(video_entry)
            except VideoFile.DoesNotExist:
                raise Http404("Video not found.")
        
        # Otherwise, return JSON metadata
        last_id = request.GET.get("last_id")  # Get last_id from query params

        #  Get the next video as a model instance
        video_entry = VideoFileForMetaSerializer.get_next_video(last_id)

        if video_entry is None:
            return Response({"error": "No more videos available."}, status=status.HTTP_404_NOT_FOUND)

        serialized_video = VideoFileForMetaSerializer(video_entry, context={'request': request})

        # Check if required fields are missing
        response_data = serialized_video.data
        missing_fields = {}

        if response_data.get('file') is None:
            missing_fields['file'] = "No file associated with this entry."

        if response_data.get('video_url') is None:
            missing_fields['video_url'] = "Video file is missing."

        if response_data.get('full_video_path') is None:
            missing_fields['full_video_path'] = "No file path found on server."

        if not response_data.get('patient_first_name'):
            missing_fields['patient_first_name'] = "Patient first name is missing."

        if not response_data.get('patient_last_name'):
            missing_fields['patient_last_name'] = "Patient last name is missing."

        if not response_data.get('patient_dob'):
            missing_fields['patient_dob'] = "Patient date of birth is missing."

        if not response_data.get('examination_date'):
            missing_fields['examination_date'] = "Examination date is missing."

        if response_data.get('duration') is None:
            missing_fields['duration'] = "Unable to determine video duration. The file might be corrupted or unreadable."

        if missing_fields:
            return Response({"error": "Missing required data.", "details": missing_fields}, 
                            status=status.HTTP_400_BAD_REQUEST)

        return Response(serialized_video.data, status=status.HTTP_200_OK)

    def serve_video_file(self, video_entry):
        """
        Streams the video file dynamically.
        """
        try:
            # Try different file sources
            file_path = None
            
            if hasattr(video_entry, 'processed_file') and video_entry.processed_file:
                file_path = video_entry.processed_file.path
            elif hasattr(video_entry, 'raw_file') and video_entry.raw_file:
                file_path = video_entry.raw_file.path
            elif hasattr(video_entry, 'file') and video_entry.file:
                file_path = video_entry.file.path

            if not file_path or not os.path.exists(file_path):
                raise Http404("Video file not found on server.")

            mime_type, _ = mimetypes.guess_type(file_path)  # Detects file type
            response = FileResponse(open(file_path, "rb"), content_type=mime_type or "video/mp4")

            response["Content-Disposition"] = f'inline; filename="{os.path.basename(file_path)}"'  # Allows direct streaming
            response["Accept-Ranges"] = "bytes"  # Enable range requests for video seeking
            response["Cache-Control"] = "public, max-age=3600"  # Cache for 1 hour

            return response  # Sends the video file as a stream

        except Exception as e:
            return Response({"error": f"Internal error: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        

    def patch(self, request, *args, **kwargs):
        """
        Calls the serializer to update `SensitiveMeta` data.
        """

        # Ensure all required fields are provided
        required_fields = ["sensitive_meta_id", "patient_first_name", "patient_last_name", "patient_dob", "examination_date"]
        missing_fields = [field for field in required_fields if field not in request.data]

        if missing_fields:
            return Response({"error": "Missing required fields", "missing_fields": missing_fields}, status=status.HTTP_400_BAD_REQUEST)

        #  Call serializer for validation
        serializer = SensitiveMetaUpdateSerializer(data=request.data, partial=True)

        if serializer.is_valid():
            #  Get the instance and update it
            sensitive_meta = SensitiveMeta.objects.get(id=request.data["sensitive_meta_id"])
            updated_instance = serializer.update(sensitive_meta, serializer.validated_data)

            return Response({
                "message": "Patient information updated successfully.",
                "updated_data": SensitiveMetaUpdateSerializer(updated_instance).data
            }, status=status.HTTP_200_OK)

        #  Return validation errors
        return Response({"error": "Invalid data.", "details": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
    

    """
    await import('https://cdn.jsdelivr.net/npm/axios/dist/axios.min.js');
const updatePatientInfo = async () => {
    const updatedData = {
        sensitive_meta_id: 6,
        patient_first_name: "Placeholder",
        patient_last_name: "Placeholder",
        patient_dob: "1994-06-15",
        examination_date: "2024-06-15"
    };

    try {
        const response = await axios.patch("http://localhost:8000/video/update_sensitivemeta/", updatedData, {
            headers: { "Content-Type": "application/json" }
        });

        console.log("Update Success:", response.data);
        alert("Patient information updated successfully!");

        return response.data;  
    } catch (error) {
        console.error("Update Error:", error.response?.data || error);
        alert("Failed to update patient information.");
        return error.response?.data || { error: "Unknown error" };  
    }
};


updatePatientInfo().then(response => console.log("Final Response:", response));

    """