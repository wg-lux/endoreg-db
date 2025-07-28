from endoreg_db.models import RawPdfFile, VideoFile
from endoreg_db.utils.permissions import DEBUG_PERMISSIONS
import logging

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)

class AvailableFilesListView(APIView):
    """
    API endpoint to list available PDFs and videos for anonymization selection.

    GET: Returns lists of available PDF and video files with their metadata
    """

    permission_classes = DEBUG_PERMISSIONS

    def _validate_pagination_params(self, request):
        """Validates and returns pagination parameters, raising ValueError on failure."""
        try:
            limit = int(request.query_params.get('limit', 50))
            offset = int(request.query_params.get('offset', 0))
        except (ValueError, TypeError):
            raise ValueError("Invalid 'limit' or 'offset' parameter. Must be integers.")

        if limit < 0 or offset < 0:
            raise ValueError("'limit' and 'offset' must be non-negative.")
        
        if limit > 100:
            logger.warning(f"Client requested limit of {limit}, capping at 100.")
            limit = 100

        return limit, offset

    def get(self, request):
        """
        List available PDF and video files for anonymization selection.

        Query Parameters:
        - type: Filter by file type ('pdf' or 'video')
        - status: Filter by anonymization status
        - limit: Number of results to return (default 50, max 100)
        - offset: Offset for pagination (default 0)

        Returns:
        {
            "pdfs": [...],
            "videos": [...],
            "total_pdfs": N,
            "total_videos": N
        }
        """
        try:
            limit, offset = self._validate_pagination_params(request)
            file_type = request.query_params.get('type', 'all').lower()
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        try:
            response_data = {}

            # Get PDFs if requested
            if file_type in ['all', 'pdf']:
                pdf_queryset = RawPdfFile.objects.select_related('sensitive_meta').all()
                total_pdfs = pdf_queryset.count()
                paginated_pdfs = pdf_queryset[offset:offset + limit]

                pdf_list = []
                for pdf in paginated_pdfs:
                    # Safely handle missing file attribute
                    file_name = 'Unknown'
                    file_path = None
                    if hasattr(pdf, 'file') and pdf.file:
                        file_name = pdf.file.name.split('/')[-1]
                        file_path = pdf.file.name
                    pdf_data = {
                        'id': pdf.id,
                        'filename': file_name,
                        'file_path': file_path,
                        'sensitive_meta_id': pdf.sensitive_meta_id,
                        'anonymized_text': getattr(pdf, 'anonymized_text', None),
                        'created_at': pdf.date_created if hasattr(pdf, 'created_at') else None,
                        'patient_info': None
                    }

                    # Add patient info if available
                    if pdf.sensitive_meta:
                        pdf_data['patient_info'] = {
                            'patient_first_name': pdf.sensitive_meta.patient_first_name,
                            'patient_last_name': pdf.sensitive_meta.patient_last_name,
                            'patient_dob': pdf.sensitive_meta.patient_dob,
                            'examination_date': pdf.sensitive_meta.examination_date,
                            'center_name': pdf.sensitive_meta.center.name if pdf.sensitive_meta.center else None
                        }

                    pdf_list.append(pdf_data)

                response_data['pdfs'] = pdf_list
                response_data['total_pdfs'] = total_pdfs

            # Get Videos if requested
            if file_type in ['all', 'video']:
                video_queryset = VideoFile.objects.select_related('sensitive_meta').all()
                total_videos = video_queryset.count()
                paginated_videos = video_queryset[offset:offset + limit]

                video_list = []
                for video in paginated_videos:
                    # Safely handle missing raw_file attribute
                    file_name = 'Unknown'
                    file_path = None
                    if hasattr(video, 'raw_file') and video.raw_file:
                        file_name = video.raw_file.name.split('/')[-1]
                        file_path = video.raw_file.name
                    video_data = {
                        'id': video.id,
                        'filename': file_name,
                        'file_path': file_path,
                        'sensitive_meta_id': video.sensitive_meta_id,
                        'patient_info': None
                    }

                    # Add patient info if available
                    if video.sensitive_meta:
                        video_data['patient_info'] = {
                            'patient_first_name': video.sensitive_meta.patient_first_name,
                            'patient_last_name': video.sensitive_meta.patient_last_name,
                            'patient_dob': video.sensitive_meta.patient_dob,
                            'examination_date': video.sensitive_meta.examination_date,
                            'center_name': video.sensitive_meta.center.name if video.sensitive_meta.center else None
                        }

                    video_list.append(video_data)

                response_data['videos'] = video_list
                response_data['total_videos'] = total_videos

            return Response(response_data)
        except Exception as e:
            logger.error(f"Error listing available files: {e}", exc_info=True)
            return Response(
                {"error": "An unexpected error occurred while fetching files."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )