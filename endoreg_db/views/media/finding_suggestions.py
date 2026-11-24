from typing import cast
from django.contrib.auth.models import Group, User
from django.core.exceptions import ObjectDoesNotExist, MultipleObjectsReturned
from django.shortcuts import get_object_or_404
from pydantic import ValidationError
from rest_framework.permissions import IsAuthenticated, BasePermission
from rest_framework.request import Request
from rest_framework.response import Response
from endoreg_db.openapi import OpenApiAPIView
from lx_dtypes.terminology.terminology_service import TerminologyError
from endoreg_db.authz.policy import satisfies
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.services.centers.access import resolve_allowed_center_ids
from endoreg_db.services.reports.finding_review import finding_suggestions


class FindingReviewPermission(BasePermission):
    def has_permission(self, request: Request, view: object) -> bool:
        del view
        user = cast(User, request.user)
        return user.is_authenticated and (
            bool(getattr(user, "is_superuser", False))
            or satisfies(
                set(Group.objects.filter(user=user).values_list("name", flat=True)),
                "patient:read",
            )
        )


class FindingSuggestionsView(OpenApiAPIView):
    permission_classes = [IsAuthenticated, FindingReviewPermission]

    def get(self, request: Request, pk: int, kind: str) -> Response:
        allowed = resolve_allowed_center_ids(request.user)
        model = RawPdfFile if kind == "pdfs" else VideoFile
        query = model.objects.all()
        if allowed is not None:
            query = query.filter(center_id__in=allowed)
        media = get_object_or_404(query, pk=pk)
        try:
            result = finding_suggestions(media)
        except (
            ValueError,
            ValidationError,
            TerminologyError,
            ObjectDoesNotExist,
            MultipleObjectsReturned,
        ):
            return Response(
                {"detail": "Befundvorschläge sind nicht konsistent konfiguriert."},
                status=409,
            )
        return Response(
            result.model_dump(mode="json"),
            headers={"Cache-Control": "private, no-store"},
        )
