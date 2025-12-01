"""
PDF Media Management View (Phase 1.2)

Provides standardized REST API for PDF files including listing, detail retrieval,
and streaming for the media management system.

This is separate from the existing pdf.PDFMediaView which handles legacy workflows.
"""

import logging
import os
from pathlib import Path

from django.db.models import Q
from django.http import FileResponse, Http404
from django.views.decorators.clickjacking import xframe_options_exempt
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from endoreg_db.models import RawPdfFile
from endoreg_db.utils.permissions import EnvironmentAwarePermission
from endoreg_db.utils.storage import file_exists

logger = logging.getLogger(__name__)


class PdfMediaView(APIView):
    """
    PDF Media Management API for CRUD operations on PDF files.

    Endpoints:
    - GET /api/media/pdfs/ - List all PDFs with filtering
    - GET /api/media/pdfs/{id}/ - Get PDF details
    - GET /api/media/pdfs/{id}/stream/ - Stream PDF file (same as detail for PDFs)
    - PATCH /api/media/pdfs/{id}/ - Update PDF metadata (future)
    - DELETE /api/media/pdfs/{id}/ - Delete PDF (future)

    Query Parameters:
    - status: Filter by processing status (not_started, done, validated)
    - search: Search in filename
    - limit: Limit results (default: 50)
    - offset: Pagination offset

    Examples:
    - GET /api/media/pdfs/?status=done&search=exam
    - GET /api/media/pdfs/123/
    - GET /api/media/pdfs/123/stream/

    Phase 1.2 Implementation:
    - List and detail views implemented
    - PDF streaming functionality
    - Filtering and search functionality
    - Pagination support
    - Error handling with proper HTTP status codes
    """

    permission_classes = [EnvironmentAwarePermission]

    def get(self, request, pk=None):
        """
        Route GET requests to listing, detail, or streaming endpoints for PDFs.
        
        Parameters:
        	request (HttpRequest): Incoming HTTP request.
        	pk (Optional[int]): If provided, the PDF primary key used for detail retrieval or streaming.
        
        Returns:
        	HttpResponse or FileResponse: JSON list/detail response for PDFs or a PDF file stream for streaming requests.
        
        Raises:
        	Http404: If the specified PDF is not found or the ID format is invalid.
        """
        if pk is not None:
            # Check if this is a streaming request
            if request.path.endswith("/stream/"):
                return self._stream_pdf(pk)
            else:
                # Detail view
                return self._get_pdf_detail(pk)
        else:
            # List view
            return self._list_pdfs(request)

    def _get_pdf_detail(self, pk):
        """
        Return detailed metadata for a specific PDF identified by its primary key.
        
        Parameters:
            pk (str|int): Primary key or identifier of the PDF to retrieve.
        
        Returns:
            dict: JSON-serializable mapping with PDF details including:
                - id
                - filename
                - file_size
                - pdf_hash
                - uploaded_at (ISO 8601 string or None)
                - anonymized_text
                - has_anonymized_text (bool)
                - is_validated (bool)
                - stream_url
                - Optional patient metadata when available:
                    - patient_first_name
                    - patient_last_name
                    - patient_dob (formatted as DD.MM.YYYY or None)
                    - examination_date (formatted as DD.MM.YYYY or None)
        
        Raises:
            Http404: If the provided `pk` is not a valid integer or no PDF with that ID exists.
        """
        try:
            # Validate pdf_id is numeric
            try:
                pdf_id_int = int(pk)
            except (ValueError, TypeError):
                raise Http404("Invalid PDF ID format")

            # Fetch PDF with related data
            pdf = RawPdfFile.objects.select_related("sensitive_meta").get(pk=pdf_id_int)

            # Build PDF details
            pdf_data = {
                "id": pdf.pk,
                "filename": getattr(pdf.file, "name", "Unknown"),
                "file_size": getattr(pdf.file, "size", 0),
                "pdf_hash": pdf.pdf_hash,
                "uploaded_at": pdf.date_created.isoformat() if getattr(pdf, "date_created", None) else None,
                "anonymized_text": pdf.anonymized_text,
                "has_anonymized_text": bool(pdf.anonymized_text and pdf.anonymized_text.strip()),
                "is_validated": getattr(pdf.sensitive_meta, "is_verified", False) if pdf.sensitive_meta else False,
                "stream_url": self.request.build_absolute_uri(f"/api/media/pdfs/{pdf.pk}/stream/"),
            }

            # Add patient metadata if available
            if pdf.sensitive_meta:
                pdf_data.update(
                    {
                        "patient_first_name": pdf.sensitive_meta.patient_first_name,
                        "patient_last_name": pdf.sensitive_meta.patient_last_name,
                        "patient_dob": pdf.sensitive_meta.patient_dob.strftime("%d.%m.%Y") if pdf.sensitive_meta.patient_dob else None,
                        "examination_date": pdf.sensitive_meta.examination_date.strftime("%d.%m.%Y") if pdf.sensitive_meta.examination_date else None,
                    }
                )

            return Response(pdf_data)

        except RawPdfFile.DoesNotExist:
            raise Http404(f"PDF with ID {pk} not found")

        except Exception as e:
            logger.error(f"Unexpected error in PDF detail view for ID {pk}: {str(e)}")
            return Response({"error": "Failed to retrieve PDF details"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @xframe_options_exempt
    def _stream_pdf(self, pk):
        """
        Stream PDF file content for viewing/download.

        Args:
            pk: PDF primary key

        Returns:
            FileResponse: PDF file stream

        Raises:
            Http404: If PDF not found or file cannot be accessed
        """
        try:
            # Validate pdf_id is numeric
            try:
                pdf_id_int = int(pk)
            except (ValueError, TypeError):
                raise Http404("Invalid PDF ID format")

            # Fetch PDF
            pdf = RawPdfFile.objects.get(pk=pdf_id_int)

            file_field = pdf.file
            file_path = file_field.path

            if not file_field or not file_field.name:
                raise Http404("PDF file not found")
            if not file_exists(file_field):
                raise Http404("PDF file does not exist in storage")


            with open(file_path, "rb") as file_handle:
                response = FileResponse(
                    file_handle,
                    content_type="application/pdf",
                    as_attachment=False,
                )

            filename = Path(file_field.name).name
            response["Content-Disposition"] = f'inline; filename="{filename}"'

            frontend_origin = os.environ.get("FRONTEND_ORIGIN", "http://localhost:8000")
            response["Access-Control-Allow-Origin"] = frontend_origin
            response["Access-Control-Allow-Credentials"] = "true"

            return response

        except RawPdfFile.DoesNotExist:
            raise Http404(f"PDF with ID {pk} not found")

        except Exception as e:
            logger.error(f"Unexpected error in PDF streaming for ID {pk}: {str(e)}")
            raise Http404("PDF file cannot be streamed")

    def _list_pdfs(self, request):
        """
        List PDFs applying status filters, search, ordering, and pagination.
        
        Supports the following query parameters:
        - search: substring match against the stored file name.
        - status: one of "not_started", "done", or "validated" to filter by processing/validation state.
        - limit: maximum number of items to return (default 50, max 100).
        - offset: zero-based index to start the page.
        
        Returns:
        A Response whose JSON payload contains:
        - count (int): total number of matching PDFs.
        - next (str|null): URL for the next page or null if none.
        - previous (str|null): URL for the previous page or null if none.
        - results (list): list of PDF objects, each containing:
            - id (int): primary key of the PDF record.
            - filename (str): stored file name or "Unknown" if unavailable.
            - file_size (int): file size in bytes (0 if unavailable).
            - pdf_hash (str): stored PDF hash value.
            - has_anonymized_text (bool): `true` if anonymized text exists and is non-empty.
            - is_validated (bool): `true` if sensitive metadata indicates verification.
            - stream_url (str): absolute URL to stream the PDF.
            - status (str): one of "not_started" (no anonymized text), "done" (anonymized text present, not validated), or "validated" (anonymized text present and validated).
        """
        try:
            # Start with all PDFs
            queryset = RawPdfFile.objects.select_related("sensitive_meta").all()

            # Apply filters
            queryset = self._apply_filters(queryset, request.query_params)

            # Apply search
            search = request.query_params.get("search", "").strip()
            if search:
                queryset = queryset.filter(Q(file__icontains=search))

            # Order by upload date (newest first) or id if no upload date
            if hasattr(queryset.model, "date_created"):
                queryset = queryset.order_by("-date_created")
            else:
                queryset = queryset.order_by("-pk")

            # Apply pagination
            limit = min(int(request.query_params.get("limit", 50)), 100)
            offset = int(request.query_params.get("offset", 0))

            total_count = queryset.count()
            pdfs = queryset[offset : offset + limit]

            # Serialize PDFs manually (no dedicated serializer yet)
            results = []
            for pdf in pdfs:
                pdf_item = {
                    "id": pdf.pk,
                    "filename": getattr(pdf.file, "name", "Unknown"),
                    "file_size": self._safe_get_file_size(pdf.file),
                    "pdf_hash": pdf.pdf_hash,
                    "has_anonymized_text": bool(pdf.anonymized_text and pdf.anonymized_text.strip()),
                    "is_validated": getattr(pdf.sensitive_meta, "is_verified", False) if pdf.sensitive_meta else False,
                    "stream_url": request.build_absolute_uri(f"/api/media/pdfs/{pdf.pk}/stream/"),
                }

                # Determine status based on anonymization and validation
                if not pdf.anonymized_text or not pdf.anonymized_text.strip():
                    pdf_item["status"] = "not_started"
                elif pdf.sensitive_meta and pdf.sensitive_meta.is_verified:
                    pdf_item["status"] = "validated"
                else:
                    pdf_item["status"] = "done"

                results.append(pdf_item)

            return Response(
                {
                    "count": total_count,
                    "next": self._get_next_url(request, offset, limit, total_count),
                    "previous": self._get_previous_url(request, offset, limit),
                    "results": results,
                }
            )

        except ValueError as e:
            return Response({"error": f"Invalid query parameter: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)

        except Exception as e:
            logger.error(f"Unexpected error in PDF list view: {str(e)}")
            return Response({"error": "Failed to retrieve PDF list"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def _safe_get_file_size(self, file_field):
        """
        Safely get file size without causing errors if file doesn't exist.

        Args:
            file_field: Django FileField

        Returns:
            int: File size in bytes, or 0 if file doesn't exist
        """
        if not file_field or not file_field.name:
            return 0

        try:
            return file_field.size
        except (OSError, IOError, ValueError):
            # File doesn't exist on disk or is corrupted
            return 0

    def _apply_filters(self, queryset, query_params):
        """
        Apply status-based filtering to a PDF queryset.
        
        If the request query parameters include a "status" key (case-insensitive, surrounding whitespace ignored), this filter narrows the queryset to PDFs matching one of these statuses:
        - "not_started": anonymized_text is missing or empty.
        - "done": anonymized_text is present and non-empty, and sensitive_meta is either missing or not verified.
        - "validated": anonymized_text is present and non-empty, and sensitive_meta.is_verified is True.
        
        Parameters:
            queryset: Django QuerySet of RawPdfFile objects to filter.
            query_params: Mapping-like object (e.g., request.query_params) containing query parameters.
        
        Returns:
            QuerySet: The filtered queryset.
        """
        status_filter = query_params.get("status", "").strip().lower()

        if status_filter:
            if status_filter == "not_started":
                # PDFs without anonymized text
                queryset = queryset.filter(Q(anonymized_text__isnull=True) | Q(anonymized_text__exact=""))
            elif status_filter == "done":
                # PDFs with anonymized text but not validated
                queryset = queryset.filter(
                    ~Q(anonymized_text__isnull=True), ~Q(anonymized_text__exact=""), Q(sensitive_meta__is_verified=False) | Q(sensitive_meta__isnull=True)
                )
            elif status_filter == "validated":
                # PDFs with anonymized text and validated
                queryset = queryset.filter(~Q(anonymized_text__isnull=True), ~Q(anonymized_text__exact=""), sensitive_meta__is_verified=True)

        return queryset

    def _get_next_url(self, request, offset, limit, total_count):
        """Generate next page URL for pagination."""
        if offset + limit >= total_count:
            return None

        next_offset = offset + limit
        return self._build_paginated_url(request, next_offset, limit)

    def _get_previous_url(self, request, offset, limit):
        """Generate previous page URL for pagination."""
        if offset <= 0:
            return None

        prev_offset = max(0, offset - limit)
        return self._build_paginated_url(request, prev_offset, limit)

    def _build_paginated_url(self, request, offset, limit):
        """Build URL with pagination parameters."""
        params = request.query_params.copy()
        params["offset"] = offset
        params["limit"] = limit

        base_url = request.build_absolute_uri(request.path)
        if params:
            return f"{base_url}?{params.urlencode()}"
        return base_url

    # Future implementation placeholders
    def patch(self, request, pk):
        """
        Update PDF metadata (Phase 1.2+ future enhancement).

        Currently returns 501 Not Implemented.
        """
        return Response({"error": "PDF metadata updates not yet implemented"}, status=status.HTTP_501_NOT_IMPLEMENTED)

    def delete(self, request, pk):
        """
        Placeholder endpoint for deleting a PDF; deletion is not implemented in this API version.
        
        Attempts to delete a PDF are rejected with HTTP 501. The response body contains an "error" message and an "alternative" field pointing to the force-remove endpoint.
        
        Returns:
            Response: DRF Response with an error message and an "alternative" instruction for DELETE /api/media-management/force-remove/{id}/, with HTTP 501 Not Implemented.
        """
        return Response(
            {"error": "PDF deletion not yet implemented", "alternative": f"Use DELETE /api/media-management/force-remove/{pk}/ instead"},
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )