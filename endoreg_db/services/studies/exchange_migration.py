"""Upgrade a portable study exchange using a locally trusted package release."""

from typing import cast
from uuid import uuid5

from lx_dtypes.models.contracts.json_types import JsonObject
from lx_dtypes.models.contracts.package_snapshot import (
    PackageSnapshot,
    restore_package_snapshot,
)
from lx_dtypes.models.contracts.reference_catalog import (
    TERMINOLOGY_REFERENCE_MODELS,
    CatalogIdentity,
)
from lx_dtypes.models.contracts.reference_migration import (
    ReferenceRowState,
    load_reference_migrations,
    plan_reference_row_migration,
)
from lx_dtypes.models.contracts.study_analysis import StudyAnalysisDefinition
from lx_dtypes.models.contracts.study_collection import StudyCollectionRecord
from lx_dtypes.models.contracts.study_exchange import StudyExchangeBundle
from lx_dtypes.models.contracts.study_metadata import (
    StudyClassificationTextRule,
    StudyFindingTextRule,
    StudyMetadata,
    StudyTerminologyRequirement,
)
from lx_dtypes.study_exchange import parse_study_exchange
from lx_dtypes.validation import assess_examination
from packaging.version import InvalidVersion, Version


class StudyTerminologyMigrationError(ValueError):
    """The source and destination packages cannot safely share the evidence."""


def is_newer_release(source: str, destination: str) -> bool:
    try:
        return Version(destination) > Version(source)
    except InvalidVersion:
        return False


def _check_package_compatibility(
    source: PackageSnapshot, destination: PackageSnapshot
) -> None:
    module = source.identity.knowledge_base_module
    version = destination.identity.knowledge_base_version
    if module != destination.identity.knowledge_base_module or not is_newer_release(
        source.identity.knowledge_base_version, version
    ):
        raise StudyTerminologyMigrationError(
            "Expected a newer release of the same package"
        )
    target = load_reference_migrations().target(module, version)
    # These fields record construction time or runtime identity, not terminology.
    runtime_fields = {"uuid", "created_at", "source_file"}
    for family, old_records in source.records.items():
        new_records = {item.name: item for item in destination.records[family]}
        kinds = [
            kind
            for kind, record_family in TERMINOLOGY_REFERENCE_MODELS.items()
            if family == record_family
        ]
        for old in old_records:
            new = new_records.get(old.name)
            if new is None:
                raise StudyTerminologyMigrationError(
                    "The current package omits a source concept"
                )
            before = cast(
                JsonObject, old.model_dump(mode="json", exclude=runtime_fields)
            )
            after = cast(
                JsonObject, new.model_dump(mode="json", exclude=runtime_fields)
            )
            if before == after:
                continue
            identity = CatalogIdentity(name=old.name)
            if (
                before.keys() != after.keys()
                or not kinds
                or not all(
                    plan_reference_row_migration(
                        ReferenceRowState(kind=kind, identity=identity, fields=before),
                        ReferenceRowState(kind=kind, identity=identity, fields=after),
                        target,
                    ).automatic
                    for kind in kinds
                )
            ):
                raise StudyTerminologyMigrationError(
                    "The current package changes source terminology without compatible migration rules"
                )


def migrate_study_exchange(
    bundle: StudyExchangeBundle,
    replacements: dict[str, PackageSnapshot],
) -> StudyExchangeBundle:
    """Build a separate version of the exchange while retaining its study links."""
    versions: dict[tuple[str, str], str] = {}
    packages: dict[str, PackageSnapshot] = {}
    for source in bundle.packages:
        destination = replacements.get(source.snapshot_id)
        if destination is None:
            packages[source.snapshot_id] = source
            continue
        _check_package_compatibility(source, destination)
        versions[
            source.identity.knowledge_base_module,
            source.identity.knowledge_base_version,
        ] = destination.identity.knowledge_base_version
        packages[destination.snapshot_id] = destination

    def update_reference[
        T: StudyTerminologyRequirement
        | StudyFindingTextRule
        | StudyClassificationTextRule
        | StudyAnalysisDefinition
    ](reference: T) -> T:
        version = versions.get((reference.module, reference.version))
        return (
            reference.model_copy(update={"version": version}) if version else reference
        )

    def update_metadata(metadata: StudyMetadata) -> StudyMetadata:
        requirements = [
            update_reference(item) for item in metadata.terminology_requirements
        ]
        return metadata.model_copy(
            update={
                "terminology_requirements": list(
                    {
                        (item.module, item.version): item for item in requirements
                    }.values()
                ),
                "inclusion_criteria": [
                    criterion.model_copy(
                        update={
                            "concepts": [
                                update_reference(item) for item in criterion.concepts
                            ]
                        }
                    )
                    for criterion in metadata.inclusion_criteria
                ],
                "analysis_definitions": [
                    update_reference(item) for item in metadata.analysis_definitions
                ],
                "text_extraction": [
                    update_reference(item) for item in metadata.text_extraction
                ],
                "classification_text_extraction": [
                    update_reference(item)
                    for item in metadata.classification_text_extraction
                ],
            }
        )

    knowledge_bases = {
        snapshot_id: restore_package_snapshot(packages[snapshot_id])
        for snapshot_id in {item.snapshot_id for item in replacements.values()}
    }
    cohorts = [
        item.model_copy(
            update={
                "definition": item.definition.model_copy(
                    update={
                        "study_metadata": update_metadata(
                            item.definition.study_metadata
                        )
                    }
                )
            }
        )
        for item in bundle.cohorts
    ]
    collections: list[StudyCollectionRecord] = []
    context = tuple(
        sorted((module, version, new) for (module, version), new in versions.items())
    )
    for item in bundle.collections:
        definition = item.cohort_definition
        if definition is not None:
            definition = definition.model_copy(
                update={"study_metadata": update_metadata(definition.study_metadata)}
            )
        destination = replacements.get(item.package_snapshot_id)
        changes: dict[str, object] = {"cohort_definition": definition}
        if destination is not None:
            record = item.record.model_copy(update=destination.identity.model_dump())
            assessment = assess_examination(
                knowledge_bases[destination.snapshot_id],
                record.model_dump(mode="python"),
                template_name=item.report_template_name,
                language=item.language,
            )
            if assessment != item.evaluation.model_copy(
                update={"knowledge_base_identity": destination.identity}
            ):
                raise StudyTerminologyMigrationError(
                    "The current package changes a stored assessment"
                )
            changes.update(
                record=record,
                evaluation=assessment,
                package_snapshot_id=destination.snapshot_id,
            )
        if destination is not None or definition != item.cohort_definition:
            # Preserve the source identity and captured assessment time in the
            # original workbook. The revalidated copy gets a replay-stable ID.
            target = (
                item.package_snapshot_id
                if destination is None
                else destination.snapshot_id
            )
            changes["record_uuid"] = uuid5(item.record_uuid, f"{target}:{context}")
        collections.append(item.model_copy(update=changes))
    try:
        return parse_study_exchange(
            bundle.model_copy(
                update={
                    "packages": list(packages.values()),
                    "cohorts": cohorts,
                    "collections": collections,
                }
            )
        )
    except ValueError as error:
        raise StudyTerminologyMigrationError(
            "The study definitions or observations cannot use the current package"
        ) from error
