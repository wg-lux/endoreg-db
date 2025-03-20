from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.http import FileResponse, Http404
import os, mimetypes
from ..models import RawPdfFile
from ..serializers.raw_pdf_anony_text_validation import RawPdfAnonyTextSerializer

class RawPdfAnonyTextView(APIView):
    """
    API for:
    - Fetching PDF metadata including `anonymized_text`.
    - Fetching the next available PDF.
    - Serving the actual PDF file.
    """

    def get(self, request):
        """
        Handles:
        - First available PDF if `last_id` is NOT provided.
        - Next available PDF if `last_id` is provided.
        - Returns the actual PDF file if `id` is provided.
        """

        pdf_id = request.GET.get("id")
        last_id = request.GET.get("last_id")

        if pdf_id:
            return self.serve_pdf_file(pdf_id)
        else:
            return self.fetch_pdf_metadata(last_id)

    def fetch_pdf_metadata(self, last_id):
        """
        Fetches the next available PDF metadata, including `anonymized_text`.
        """
        pdf_entry = RawPdfAnonyTextSerializer.get_next_pdf(last_id)

        if pdf_entry is None:
            return Response({"error": "No more PDFs available."}, status=status.HTTP_404_NOT_FOUND)

        serialized_pdf = RawPdfAnonyTextSerializer(pdf_entry, context={'request': self.request})
        return Response(serialized_pdf.data, status=status.HTTP_200_OK)

    def serve_pdf_file(self, pdf_id):
        """
        Serves the actual PDF file for viewing.
        """
        try:
            pdf_entry = RawPdfFile.objects.get(id=pdf_id)
            if not pdf_entry.file:
                return Response({"error": "PDF file not found."}, status=status.HTTP_404_NOT_FOUND)

            full_pdf_path = pdf_entry.file.path
            if not os.path.exists(full_pdf_path):
                raise Http404("PDF file not found on server.")

            mime_type, _ = mimetypes.guess_type(full_pdf_path)
            response = FileResponse(open(full_pdf_path, "rb"), content_type=mime_type or "application/pdf")
            response["Content-Disposition"] = f'inline; filename="{os.path.basename(full_pdf_path)}"'
            return response

        except RawPdfFile.DoesNotExist:
            return Response({"error": "Invalid PDF ID."}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({"error": f"Internal error: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class UpdateAnonymizedTextView(APIView):
    """
    API to update only `anonymized_text` in `RawPdfFile`
    """

    def patch(self, request):
        """
        Updates `anonymized_text` for a given `pdf_id`.
        """
        pdf_id = request.data.get("id")

        if not pdf_id:
            return Response({"error": "pdf_id is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            pdf_entry = RawPdfFile.objects.get(id=pdf_id)
        except RawPdfFile.DoesNotExist:
            return Response({"error": "PDF not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = RawPdfAnonyTextSerializer(pdf_entry, data=request.data, partial=True)

        if serializer.is_valid():
            serializer.save()
            return Response({"message": "PDF anonymized_text updated successfully.", "updated_data": serializer.data},
                            status=status.HTTP_200_OK)

        return Response({"error": "Invalid data.", "details": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
