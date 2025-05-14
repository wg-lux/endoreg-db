from django.http import FileResponse, Http404
import mimetypes
import os
from ..models import RawPdfFile
from ..serializers._old.raw_pdf_meta_validation import PDFFileForMetaSerializer, SensitiveMetaUpdateSerializer
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from ..models import SensitiveMeta

class PDFFileForMetaView(APIView):
    """
    API endpoint to:
    - Fetch PDF metadata if `id` is NOT provided.
    - Serve the actual PDF file if `id` is provided.
    """

    def get(self, request):
        """
        Handles both:
        Fetching PDF metadata** (if `id` is NOT provided)
         Serving the actual PDF file** (if `id` is provided)
        """

        pdf_id = request.GET.get("id")  # Check if 'id' is provided in the query params
        last_id = request.GET.get("last_id")  # Check if 'last_id' is provided for pagination

        if pdf_id:
            return self.serve_pdf_file(pdf_id)  # Serve the actual PDF file
        else:
            return self.fetch_pdf_metadata(last_id)  # Fetch metadata for the first or next PDF

    def fetch_pdf_metadata(self, last_id):
        """
        Fetches the first or next available PDF metadata.
        """
        pdf_entry = PDFFileForMetaSerializer.get_next_pdf(last_id)

        if pdf_entry is None:
            return Response({"error": "No more PDFs available."}, status=status.HTTP_404_NOT_FOUND)

        serialized_pdf = PDFFileForMetaSerializer(pdf_entry, context={'request': self.request})

        print("Debugging API Response:")
        print("Serialized Data:", serialized_pdf.data)  # Debugging
        return Response(serialized_pdf.data, status=status.HTTP_200_OK)
    
    def serve_pdf_file(self, pdf_id):
        """
        Serves the actual PDF file for download or viewing.
        """
        try:
            pdf_entry = RawPdfFile.objects.get(id=pdf_id)  # Get the PDF file by ID

            if not pdf_entry.file:
                return Response({"error": "PDF file not found."}, status=status.HTTP_404_NOT_FOUND)

            full_pdf_path = pdf_entry.file.path  # Get the absolute file path

            if not os.path.exists(full_pdf_path):
                raise Http404("PDF file not found on server.")

            mime_type, _ = mimetypes.guess_type(full_pdf_path)  # Detect file type
            response = FileResponse(open(full_pdf_path, "rb"), content_type=mime_type or "application/pdf")

            response["Content-Disposition"] = f'inline; filename="{os.path.basename(full_pdf_path)}"'  # Allows direct viewing

            return response  # Sends the PDF file as a stream

        except RawPdfFile.DoesNotExist:
            return Response({"error": "Invalid PDF ID."}, status=status.HTTP_400_BAD_REQUEST)

        except Exception as e:
            return Response({"error": f"Internal error: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)



class UpdateSensitiveMetaView(APIView):
    """
    API endpoint to update patient details in the SensitiveMeta table.
    Handles partial updates (only edited fields).
    """

    def patch(self, request, *args, **kwargs):
        """
        Updates the provided fields for a specific patient record.
        Only updates fields that are sent in the request.
        """
        sensitive_meta_id = request.data.get("sensitive_meta_id")  # Required field

        if not sensitive_meta_id:
            return Response({"error": "sensitive_meta_id is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            sensitive_meta = SensitiveMeta.objects.get(id=sensitive_meta_id)
        except SensitiveMeta.DoesNotExist:
            return Response({"error": "Patient record not found."}, status=status.HTTP_404_NOT_FOUND)

        # Serialize the request data with partial=True to allow partial updates
        serializer = SensitiveMetaUpdateSerializer(sensitive_meta, data=request.data, partial=True)

        if serializer.is_valid():
            serializer.save()
            return Response({"message": "Patient information updated successfully.", "updated_data": serializer.data},
                            status=status.HTTP_200_OK)
        
        return Response({"error": "Invalid data.", "details": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
