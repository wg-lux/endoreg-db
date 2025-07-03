# api/views/anonymization_overview.py
from rest_framework.generics import ListAPIView
from rest_framework.pagination import PageNumberPagination
#from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.db.models import Prefetch, QuerySet
from endoreg_db.models import VideoFile, RawPdfFile
from ..serializers.file_overview_serializer import FileOverviewSerializer
from ..utils.permissions import DEBUG_PERMISSIONS  # <-- adapt import path

class NoPagination(PageNumberPagination):
    page_size = None


class AnonymizationOverviewView(ListAPIView):
    """
    GET /api/anonymization/items/overview/
    --------------------------------------
    Returns a flat list (Video + PDF) ordered by newest upload first.
    """
    serializer_class = FileOverviewSerializer
    permission_classes = DEBUG_PERMISSIONS   
    pagination_class = NoPagination

    def get_queryset(self) -> QuerySet:                  # type: ignore
        qs_video = (
            VideoFile.objects
            .select_related("state")
            .prefetch_related(
                Prefetch(
                    "label_video_segments",
                    queryset=VideoFile.label_video_segments.rel.related_model.objects
                             .filter(state__is_validated=True)
                             .only("id")                # we only need existence
                )
            )
            .only("id", "original_file_name", "raw_file", "uploaded_at", "state")
        )
        qs_pdf = (
            RawPdfFile.objects
            .select_related("sensitive_meta")
            .only("id", "file", "created_at", "anonymized", "sensitive_meta")
        )
        # union() requires same columns; we just merge in Python later
        return list(qs_video) + list(qs_pdf)

    def list(self, request, *args, **kwargs):
        items = sorted(self.get_queryset(), key=lambda obj: getattr(obj, "uploaded_at", None), reverse=True)
        serializer = self.get_serializer(items, many=True)
        return Response(serializer.data)
