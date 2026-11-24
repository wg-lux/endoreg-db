"""Owner-scoped CRUD for persistent studies, without clinical record deletion."""

from django.db import transaction
from django.shortcuts import get_object_or_404
from lx_dtypes.models.contracts.study import StudyDefinition
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from endoreg_db.models.other.study import Study
from endoreg_db.openapi import OpenApiAPIView
from endoreg_db.views.media.table_mappings import TableImportPermission


def study_response(study: Study) -> dict[str, object]:
    return {
        "id": study.pk,
        "uuid": str(study.uuid),
        "name": study.name,
        "hypothesis": study.hypothesis,
        "description": study.description,
        "created_by_id": study.created_by_id,
        "created_at": study.created_at.isoformat(),
        "updated_at": study.updated_at.isoformat(),
    }


class StudiesView(OpenApiAPIView):
    permission_classes = [IsAuthenticated, TableImportPermission]

    def get(self, request: Request) -> Response:
        return Response(
            [
                study_response(study)
                for study in Study.objects.filter(created_by_id=request.user.pk)
            ]
        )

    def post(self, request: Request) -> Response:
        try:
            definition = StudyDefinition.model_validate(request.data)
        except ValueError:
            return Response({"detail": "Invalid study definition"}, status=400)
        study = Study.objects.create(
            created_by_id=request.user.pk, **definition.model_dump()
        )
        return Response(study_response(study), status=201)


class StudyDetailView(OpenApiAPIView):
    permission_classes = [IsAuthenticated, TableImportPermission]

    def get(self, request: Request, study_id: int) -> Response:
        return Response(
            study_response(
                get_object_or_404(Study, pk=study_id, created_by_id=request.user.pk)
            )
        )

    def put(self, request: Request, study_id: int) -> Response:
        try:
            definition = StudyDefinition.model_validate(request.data)
        except ValueError:
            return Response({"detail": "Invalid study definition"}, status=400)
        with transaction.atomic():
            study = get_object_or_404(
                Study.objects.select_for_update(),
                pk=study_id,
                created_by_id=request.user.pk,
            )
            study.name = definition.name
            study.hypothesis = definition.hypothesis
            study.description = definition.description
            study.save(
                update_fields=["name", "hypothesis", "description", "updated_at"]
            )
        return Response(study_response(study))

    def delete(self, request: Request, study_id: int) -> Response:
        with transaction.atomic():
            study = get_object_or_404(
                Study.objects.select_for_update(),
                pk=study_id,
                created_by_id=request.user.pk,
            )
            study.delete()
        return Response(status=204)
