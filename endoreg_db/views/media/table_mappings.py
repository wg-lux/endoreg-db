"""Authenticated mapping authoring; documents never grant patient access."""

from __future__ import annotations

from importlib.resources import files
from typing import cast
from uuid import UUID, uuid4

import yaml
from django.contrib.auth.models import Group, User
from django.http import Http404
from pydantic import BaseModel, ConfigDict, Field
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from endoreg_db.authz.policy import satisfies
from endoreg_db.models.administration.center.center import Center
from endoreg_db.openapi import OpenApiAPIView
from endoreg_db.services.centers.access import resolve_allowed_center_ids
from endoreg_db.services.studies.table_mappings import (
    list_table_mappings,
    mapping_path,
    save_table_mapping,
)
from endoreg_db.services.studies.table_parser import (
    load_table_mapping,
    parse_table_mapping,
)


class TableImportPermission(BasePermission):
    def has_permission(self, request: Request, view: object) -> bool:
        del view
        if not request.user.is_authenticated:
            return False
        user = cast(User, request.user)
        role = (
            "patient:read"
            if request.method in {"GET", "HEAD", "OPTIONS"}
            else "patient:write"
        )
        return bool(getattr(user, "is_superuser", False)) or satisfies(
            set(Group.objects.filter(user=user).values_list("name", flat=True)), role
        )


class _MappingSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    yaml: str = Field(max_length=1_048_576)


class TableMappingsView(OpenApiAPIView):
    permission_classes = [IsAuthenticated, TableImportPermission]

    def get(self, request: Request) -> Response:
        allowed = resolve_allowed_center_ids(request.user)
        centers = Center.objects.all()
        if allowed is not None:
            centers = centers.filter(pk__in=allowed)
        template = parse_table_mapping(
            files("endoreg_db")
            .joinpath("data/study_import/colonoscopy.yml")
            .read_text(encoding="utf-8")
        ).model_copy(update={"mapping_id": uuid4()})
        return Response(
            {
                "template": yaml.safe_dump(
                    template.model_dump(mode="json"), sort_keys=False
                ),
                "mappings": [
                    {"id": str(spec.mapping_id), "name": spec.name}
                    for spec in list_table_mappings(cast(int, request.user.pk))
                ],
                "centers": list(
                    centers.order_by("name").values("id", "name", "center_key")
                ),
            },
            headers={"Cache-Control": "private, no-store"},
        )

    def post(self, request: Request) -> Response:
        try:
            submission = _MappingSubmission.model_validate(request.data)
            spec = save_table_mapping(cast(int, request.user.pk), submission.yaml)
        except FileExistsError:
            return Response(
                {
                    "detail": "Mapping ID already has different content; use a new mapping ID."
                },
                status=409,
            )
        except ValueError:
            return Response(
                {
                    "detail": "Invalid mapping YAML. Check required identity fields and mapping schema."
                },
                status=400,
            )
        return Response({"id": str(spec.mapping_id), "name": spec.name}, status=201)


class TableMappingDetailView(OpenApiAPIView):
    permission_classes = [IsAuthenticated, TableImportPermission]

    def get(self, request: Request, mapping_id: UUID) -> Response:
        try:
            spec = load_table_mapping(
                mapping_path(cast(int, request.user.pk), mapping_id)
            )
        except FileNotFoundError as error:
            raise Http404 from error
        return Response(
            {"yaml": yaml.safe_dump(spec.model_dump(mode="json"), sort_keys=False)},
            headers={"Cache-Control": "private, no-store"},
        )
