"""User-owned immutable mapping documents in canonical protected runtime storage."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import yaml
from lx_dtypes.models.contracts.knowledge_base import (
    KnowledgeBaseIdentity,
    validate_optional_knowledge_base_identity,
)
from lx_dtypes.models.contracts.study_metadata import StudyMetadata
from lx_dtypes.models.contracts.table_import import TableMapping
from lx_dtypes.packages import load_registered_package
from lx_dtypes.serialization import parse_str_list
from pydantic import BaseModel, ConfigDict

from endoreg_db.services.studies.table_parser import (
    load_table_mapping,
    parse_table_mapping,
)
from endoreg_db.utils.file_operations import atomic_create_file, ensure_directory
from endoreg_db.utils.paths import ensure_within_storage_root, get_runtime_paths


def cohort_table_packages(owner_id: int, cohort_id: int) -> list[KnowledgeBaseIdentity]:
    """Only an owned cohort defines which package identities a table may use."""
    from endoreg_db.models.other.study_cohort import StudyCohort

    try:
        cohort = StudyCohort.objects.get(pk=cohort_id, created_by_id=owner_id)
    except StudyCohort.DoesNotExist as error:
        raise PermissionError("Cohort access denied") from error
    return table_packages_from_metadata(
        StudyMetadata.model_validate(cohort.study_metadata)
    )


def table_packages_from_metadata(
    metadata: StudyMetadata,
) -> list[KnowledgeBaseIdentity]:
    return [
        KnowledgeBaseIdentity(
            knowledge_base_module=item.module, knowledge_base_version=item.version
        )
        for item in metadata.terminology_requirements
    ]


def select_cohort_table_package(
    packages: list[KnowledgeBaseIdentity], module: str | None, version: str | None
) -> KnowledgeBaseIdentity | None:
    selected = validate_optional_knowledge_base_identity(module, version)
    if selected is None:
        return packages[0] if len(packages) == 1 else None
    if selected not in packages:
        raise ValueError("Mapping package must match the cohort requirements")
    return selected


def mapping_directory(owner_id: int) -> Path:
    if owner_id < 1:
        raise ValueError("A persisted mapping owner is required")
    root = get_runtime_paths().table_mappings
    candidate = ensure_within_storage_root(root / str(owner_id))
    if not candidate.is_relative_to(root.resolve()):
        raise ValueError("Mapping directory escapes its root")
    return candidate


def mapping_path(owner_id: int, mapping_id: UUID) -> Path:
    root = mapping_directory(owner_id)
    path = ensure_within_storage_root(root / f"{mapping_id}.yml")
    if path.parent != root:
        raise ValueError("Mapping file escapes its owner directory")
    return path


def save_table_mapping(owner_id: int, raw: str) -> TableMapping:
    spec = parse_table_mapping(raw)
    destination = mapping_path(owner_id, spec.mapping_id)
    ensure_directory(destination.parent, dir_mode=0o700)
    content = yaml.safe_dump(spec.model_dump(mode="json"), sort_keys=False).encode(
        "utf-8"
    )
    try:
        atomic_create_file(
            destination=destination, content=[content], file_mode=0o600, dir_mode=0o700
        )
    except FileExistsError:
        if load_table_mapping(destination) != spec:
            raise FileExistsError(
                "Mapping ID already exists with different content"
            ) from None
    return spec


def list_table_mappings(owner_id: int) -> list[TableMapping]:
    root = mapping_directory(owner_id)
    if not root.exists():
        return []
    return [
        load_table_mapping(mapping_path(owner_id, UUID(path.stem)))
        for path in sorted(root.glob("*.yml"))
    ]


class VocabularyExamination(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    findings: list[str]


class VocabularyFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    classifications: list[str]


class VocabularyClassification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    choices: list[str]


class TableMappingVocabulary(BaseModel):
    """Identifiers a mapping may target; the importer validates against the same KB."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    knowledge_base: KnowledgeBaseIdentity
    examinations: list[VocabularyExamination]
    findings: list[VocabularyFinding]
    classifications: list[VocabularyClassification]


def mapping_vocabulary(identity: KnowledgeBaseIdentity) -> TableMappingVocabulary:
    # Record lists flatten list fields to comma-separated text.
    records = load_registered_package(
        identity.knowledge_base_module, version=identity.knowledge_base_version
    ).export_record_lists()
    return TableMappingVocabulary(
        knowledge_base=identity,
        examinations=sorted(
            (
                VocabularyExamination(
                    name=item["name"], findings=sorted(parse_str_list(item["findings"]))
                )
                for item in records["examinations"]
            ),
            key=lambda item: item.name,
        ),
        findings=sorted(
            (
                VocabularyFinding(
                    name=item["name"],
                    classifications=sorted(parse_str_list(item["classifications"])),
                )
                for item in records["findings"]
            ),
            key=lambda item: item.name,
        ),
        classifications=sorted(
            (
                VocabularyClassification(
                    name=item["name"],
                    choices=sorted(parse_str_list(item["classification_choices"])),
                )
                for item in records["classifications"]
            ),
            key=lambda item: item.name,
        ),
    )
