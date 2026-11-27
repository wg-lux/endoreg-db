"""Authenticated mapping authoring; documents never grant patient access."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import yaml
from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import UploadedFile
from django.http import Http404
from lx_dtypes.models.contracts.knowledge_base import KnowledgeBaseIdentity
from lx_dtypes.models.contracts.table_import import (
    TableExtractionError,
    TableMapping,
    TableSource,
)
from lx_dtypes.terminology.terminology_service import TerminologyError
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from endoreg_db.authz.policy import satisfies
from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.other.gender import Gender
from endoreg_db.openapi import OpenApiAPIView
from endoreg_db.services.centers.access import resolve_allowed_center_ids
from endoreg_db.services.studies.table_mappings import (
    cohort_table_packages,
    list_table_mappings,
    mapping_path,
    mapping_vocabulary,
    save_table_mapping,
    select_cohort_table_package,
)
from endoreg_db.services.studies.table_parser import (
    MAX_INSPECTED_VALUE_COLUMNS,
    MAX_TABLE_BYTES,
    inspect_table,
    load_table_mapping,
    parse_table_mapping,
    spacy_patient_extractor,
)
from endoreg_db.services.studies.table_prefill import default_prefill_rules


class TableImportPermission(BasePermission):
    def has_permission(self, request: Request, view: object) -> bool:
        del view
        if not request.user.is_authenticated or not request.user.is_active:
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


def _template_mapping() -> TableMapping:
    return parse_table_mapping(
        files("endoreg_db")
        .joinpath("data/study_import/colonoscopy.yml")
        .read_text(encoding="utf-8")
    ).model_copy(update={"mapping_id": uuid4()})


class TableMappingsView(OpenApiAPIView):
    permission_classes = [IsAuthenticated, TableImportPermission]

    def get(self, request: Request) -> Response:
        allowed = resolve_allowed_center_ids(request.user)
        centers = Center.objects.all()
        if allowed is not None:
            centers = centers.filter(pk__in=allowed)
        template = _template_mapping()
        packages: list[KnowledgeBaseIdentity] = []
        selected: KnowledgeBaseIdentity | None = None
        scoped = request.query_params.get("cohort_id")
        if scoped is not None:
            try:
                packages = cohort_table_packages(
                    cast(int, request.user.pk), int(scoped)
                )
                selected = select_cohort_table_package(
                    packages,
                    request.query_params.get("module"),
                    request.query_params.get("version"),
                )
                if selected is not None:
                    template = template.model_copy(update={"knowledge_base": selected})
            except PermissionError:
                return Response({"detail": "Cohort access denied."}, status=403)
            except ValueError:
                return Response(
                    {
                        "detail": "Choose a package declared by the cohort.",
                        "code": "cohort_package_mismatch",
                    },
                    status=409,
                )
        return Response(
            {
                "packages": [item.model_dump(mode="json") for item in packages],
                "template": yaml.safe_dump(
                    template.model_dump(mode="json"), sort_keys=False
                )
                if scoped is None or selected is not None
                else "",
                "mappings": [
                    {"id": str(spec.mapping_id), "name": spec.name}
                    for spec in list_table_mappings(cast(int, request.user.pk))
                    if scoped is None or spec.knowledge_base == selected
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


class _InspectionOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source: TableSource = Field(default_factory=TableSource)
    value_columns: list[str] = Field(
        default_factory=list, max_length=MAX_INSPECTED_VALUE_COLUMNS
    )
    prefill: bool = False
    cohort_id: int | None = Field(default=None, gt=0)
    knowledge_base: KnowledgeBaseIdentity | None = None


class TableMappingInspectionView(OpenApiAPIView):
    """Headers, distinct cells and KB identifiers for authoring a mapping.

    The upload is read in memory and discarded; nothing is persisted or logged.
    Distinct values come only from columns the caller names, which are the
    columns they map to terminology.
    """

    permission_classes = [IsAuthenticated, TableImportPermission]

    def post(self, request: Request) -> Response:
        upload = request.FILES.get("file")
        if not isinstance(upload, UploadedFile) or not upload.name:
            return Response({"detail": "A table upload is required."}, status=400)
        suffix = Path(upload.name).suffix.lower()
        if (
            suffix not in {".csv", ".xls", ".xlsx"}
            or upload.size is None
            or upload.size > MAX_TABLE_BYTES
        ):
            return Response(
                {"detail": "Upload a CSV, XLSX or XLS table of at most 20 MiB."},
                status=400,
            )
        try:
            options = _InspectionOptions.model_validate_json(
                str(request.data.get("options", "{}"))
            )
        except ValidationError:
            return Response({"detail": "Invalid table source options."}, status=400)
        content = upload.read(MAX_TABLE_BYTES + 1)
        try:
            inspection = inspect_table(
                content,
                suffix,
                options.source,
                value_columns=options.value_columns,
                prefill_rules=default_prefill_rules() if options.prefill else None,
                patient_extractor=spacy_patient_extractor,
            )
            identity = options.knowledge_base
            if options.cohort_id is not None:
                packages = cohort_table_packages(
                    cast(int, request.user.pk), options.cohort_id
                )
                if identity is None and len(packages) == 1:
                    identity = packages[0]
                if identity not in packages:
                    return Response(
                        {
                            "detail": "Choose a package declared by the cohort.",
                            "code": "cohort_package_mismatch",
                        },
                        status=409,
                    )
            vocabulary = mapping_vocabulary(
                identity or _template_mapping().knowledge_base
            )
        except PermissionError:
            return Response({"detail": "Cohort access denied."}, status=403)
        except TableExtractionError as error:
            return Response(
                {
                    "detail": str(error),
                    "row_number": error.row_number,
                    "field": error.field,
                    "code": error.code,
                },
                status=400,
            )
        except TerminologyError:
            return Response(
                {"detail": "Selected knowledge-base version is unavailable."},
                status=409,
            )
        except ValueError:
            return Response(
                {
                    "detail": "Table could not be read with these source settings. Check the delimiter, encoding, sheet and header row."
                },
                status=400,
            )
        return Response(
            {
                **inspection.model_dump(mode="json"),
                "vocabulary": {
                    **vocabulary.model_dump(mode="json"),
                    "genders": list(
                        Gender.objects.order_by("name").values_list("name", flat=True)
                    ),
                },
            },
            headers={"Cache-Control": "private, no-store"},
        )
