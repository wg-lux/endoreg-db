"""Fail-closed projection of a resolved, versioned clinical knowledge graph."""

import logging
from typing import Literal, cast

from django.db import connection, models, transaction
from lx_dtypes.models.contracts.core_concepts import CoreConceptBase
from lx_dtypes.models.contracts.json_types import JsonObject
from lx_dtypes.models.contracts.knowledge_base import KnowledgeBaseIdentity
from lx_dtypes.models.contracts.knowledge_base_graph import (
    GraphNodeKind,
    KnowledgeBaseGraphSnapshot,
    build_knowledge_base_graph_snapshot,
)
from pydantic import BaseModel
from lx_dtypes.models.interface.KnowledgeBase import KnowledgeBase

from endoreg_db.models.medical.examination.examination import Examination
from endoreg_db.models.medical.examination.examination_type import ExaminationType
from endoreg_db.models.medical.examination.examination_indication import (
    ExaminationIndication,
    ExaminationIndicationClassification,
    ExaminationIndicationClassificationChoice,
)
from endoreg_db.models.medical.finding.finding import Finding
from endoreg_db.models.medical.finding.finding_type import FindingType
from endoreg_db.models.medical.finding.finding_intervention import (
    FindingIntervention,
    FindingInterventionType,
)
from endoreg_db.models.medical.finding.finding_classification import (
    FindingClassification,
    FindingClassificationChoice,
    FindingClassificationType,
)
from endoreg_db.models.other.clinical_reference_import import ClinicalReferenceImport
from endoreg_db.models.other.information_source import InformationSource
from endoreg_db.models.other.unit import Unit
from endoreg_db.schemas.clinical_reference import (
    ClinicalReferencePlan,
    ClinicalReferenceReceipt,
    ReferenceBinding,
    ReferenceChange,
    ReferenceDefinition,
    ReferenceDifference,
    ReferenceKey,
    ReferenceModel,
)
from endoreg_db.utils.file_operations import advisory_file_lock
from endoreg_db.utils.paths import get_runtime_paths
from endoreg_db.utils.structured_logging import emit_structured_event

from endoreg_db.helpers.typing import ReferenceRelation

logger = logging.getLogger(__name__)


MODEL_TYPES: dict[ReferenceModel, type[models.Model]] = {
    ReferenceModel.UNIT: Unit,
    ReferenceModel.FINDING_TYPE: FindingType,
    ReferenceModel.INTERVENTION_TYPE: FindingInterventionType,
    ReferenceModel.INTERVENTION: FindingIntervention,
    ReferenceModel.CLASSIFICATION_TYPE: FindingClassificationType,
    ReferenceModel.CLASSIFICATION: FindingClassification,
    ReferenceModel.CHOICE: FindingClassificationChoice,
    ReferenceModel.FINDING: Finding,
    ReferenceModel.INDICATION_CLASSIFICATION: ExaminationIndicationClassification,
    ReferenceModel.INDICATION_CHOICE: ExaminationIndicationClassificationChoice,
    ReferenceModel.INDICATION: ExaminationIndication,
    ReferenceModel.EXAMINATION_TYPE: ExaminationType,
    ReferenceModel.EXAMINATION: Examination,
    ReferenceModel.INFORMATION_SOURCE: InformationSource,
}

_GRAPH_KINDS: dict[ReferenceModel, tuple[GraphNodeKind, ...]] = {
    ReferenceModel.UNIT: ("unit",),
    ReferenceModel.FINDING_TYPE: ("finding_type",),
    ReferenceModel.INTERVENTION_TYPE: ("intervention_type",),
    ReferenceModel.INTERVENTION: ("intervention",),
    ReferenceModel.CLASSIFICATION_TYPE: ("classification_type",),
    ReferenceModel.CLASSIFICATION: ("classification",),
    ReferenceModel.CHOICE: ("classification_choice",),
    ReferenceModel.FINDING: ("finding",),
    ReferenceModel.INDICATION_CLASSIFICATION: ("indication_type", "classification"),
    ReferenceModel.INDICATION_CHOICE: ("classification_choice",),
    ReferenceModel.INDICATION: ("indication",),
    ReferenceModel.EXAMINATION_TYPE: ("examination_type",),
    ReferenceModel.EXAMINATION: ("examination",),
    ReferenceModel.INFORMATION_SOURCE: ("information_source",),
}


def _semantic_closure(
    snapshot: KnowledgeBaseGraphSnapshot, key: ReferenceKey
) -> dict[tuple[GraphNodeKind, str], BaseModel]:
    """A shared ORM identity must keep its full transitive clinical meaning."""
    kinds: tuple[GraphNodeKind, ...] = (
        "unit",
        "unit_type",
        "finding_type",
        "finding",
        "classification_type",
        "classification",
        "classification_choice",
        "classification_choice_descriptor",
        "intervention_type",
        "intervention",
        "indication_type",
        "indication",
        "examination_type",
        "examination",
        "information_source",
        "information_source_type",
        "citation",
    )
    records: dict[tuple[GraphNodeKind, str], BaseModel] = {}
    for kind in kinds:
        for record in cast(list[CoreConceptBase], getattr(snapshot.concepts, kind)):
            records[(kind, record.name)] = record
    for template in snapshot.report_templates:
        records[("report_template", template.name)] = template
    pending: list[tuple[GraphNodeKind, str]] = [
        (kind, key.name)
        for kind in _GRAPH_KINDS[key.model]
        if (kind, key.name) in records
    ]
    if key.model == ReferenceModel.EXAMINATION:
        pending.extend(
            ("report_template", t.name)
            for t in snapshot.report_templates
            if t.examination == key.name
        )
    result: dict[tuple[GraphNodeKind, str], BaseModel] = {}
    while pending:
        node = pending.pop()
        if node in result:
            continue
        result[node] = records[node]
        pending.extend(
            (edge.target.kind, edge.target.name)
            for edge in snapshot.edges
            if (edge.source.kind, edge.source.name) == node
        )
    return result


def _references(model: ReferenceModel, names: list[str]) -> list[ReferenceKey]:
    return [ReferenceKey(model=model, name=name) for name in names]


def compile_clinical_references(
    snapshot: KnowledgeBaseGraphSnapshot,
) -> list[ReferenceDefinition]:
    """Compile ORM projections; the complete typed snapshot preserves other fields.

    Descriptors, units' types, provenance catalogs and templates remain in the
    immutable graph, rather than being squeezed into legacy choice JSON shapes.
    """
    snapshot = KnowledgeBaseGraphSnapshot.model_validate(snapshot.model_dump())
    concepts = snapshot.concepts
    definitions: list[ReferenceDefinition] = []

    def add(
        model: ReferenceModel,
        record: CoreConceptBase,
        relations: dict[str, list[ReferenceKey]] | None = None,
        *,
        description: bool = True,
        attributes: dict[str, str | None] | None = None,
    ) -> None:
        values: dict[str, str | None] = (
            {"description": record.description or ""} if description else {}
        )
        values.update(attributes or {})
        definition = ReferenceDefinition(
            model=model, name=record.name, attributes=values, relations=relations or {}
        )
        row = MODEL_TYPES[model](name=record.name, **values)
        for field_name in ("name", *values):
            # Validate length/null constraints before the transaction mutates anything.
            field = row._meta.get_field(field_name)
            if not isinstance(field, models.Field):
                raise ValueError(
                    "Clinical scalar projection targets a reverse relation"
                )
            field.clean(getattr(row, field_name), row)
        definitions.append(definition)

    for record in concepts.unit:
        add(
            ReferenceModel.UNIT,
            record,
            attributes={"abbreviation": record.abbreviation},
        )
    for record in concepts.information_source:
        add(ReferenceModel.INFORMATION_SOURCE, record)
    for record in concepts.finding_type:
        add(ReferenceModel.FINDING_TYPE, record)
    for record in concepts.intervention_type:
        add(ReferenceModel.INTERVENTION_TYPE, record)
    for record in concepts.classification_type:
        add(ReferenceModel.CLASSIFICATION_TYPE, record)
    for record in concepts.classification_choice:
        add(ReferenceModel.CHOICE, record)
    for record in concepts.classification:
        add(
            ReferenceModel.CLASSIFICATION,
            record,
            {
                "classification_types": _references(
                    ReferenceModel.CLASSIFICATION_TYPE, record.classification_types
                ),
                "choices": _references(
                    ReferenceModel.CHOICE, record.classification_choices
                ),
            },
        )
    for record in concepts.intervention:
        add(
            ReferenceModel.INTERVENTION,
            record,
            {
                "intervention_types": _references(
                    ReferenceModel.INTERVENTION_TYPE, record.intervention_types
                ),
            },
        )
    for record in concepts.finding:
        add(
            ReferenceModel.FINDING,
            record,
            {
                "finding_types": _references(
                    ReferenceModel.FINDING_TYPE, record.finding_types
                ),
                "finding_interventions": _references(
                    ReferenceModel.INTERVENTION, record.interventions
                ),
                "caused_by_interventions": _references(
                    ReferenceModel.INTERVENTION, record.caused_by_interventions
                ),
                "finding_classifications": _references(
                    ReferenceModel.CLASSIFICATION, record.classifications
                ),
            },
        )
    for record in concepts.indication_type:
        add(ReferenceModel.INDICATION_CLASSIFICATION, record)
    indication_classifications = {
        name for item in concepts.indication for name in item.classifications
    }
    indication_choices = {
        name
        for item in concepts.classification
        if item.name in indication_classifications
        for name in item.classification_choices
    }
    for record in concepts.classification_choice:
        if record.name in indication_choices:
            add(ReferenceModel.INDICATION_CHOICE, record, description=False)
    for record in concepts.classification:
        if record.name in indication_classifications:
            add(
                ReferenceModel.INDICATION_CLASSIFICATION,
                record,
                {
                    "choices": _references(
                        ReferenceModel.INDICATION_CHOICE, record.classification_choices
                    ),
                },
            )
    for record in concepts.indication:
        add(
            ReferenceModel.INDICATION,
            record,
            {
                "classifications": _references(
                    ReferenceModel.INDICATION_CLASSIFICATION,
                    list(
                        dict.fromkeys(record.indication_types + record.classifications)
                    ),
                ),
                "expected_interventions": _references(
                    ReferenceModel.INTERVENTION, record.interventions
                ),
            },
        )
    for record in concepts.examination_type:
        add(ReferenceModel.EXAMINATION_TYPE, record, description=False)
    for record in concepts.examination:
        add(
            ReferenceModel.EXAMINATION,
            record,
            {
                "examination_types": _references(
                    ReferenceModel.EXAMINATION_TYPE, record.examination_types
                ),
                "indications": _references(
                    ReferenceModel.INDICATION, record.indications
                ),
                "findings": _references(ReferenceModel.FINDING, record.findings),
            },
        )
    keys = {d.identity for d in definitions}
    if len(keys) != len(definitions):
        raise ValueError("Clinical graph has conflicting host projection identities")
    for definition in definitions:
        for refs in definition.relations.values():
            if any(ref.identity not in keys for ref in refs):
                raise ValueError("Clinical projection has unresolved references")
    return definitions


def clinical_snapshot(knowledge_base: KnowledgeBase) -> KnowledgeBaseGraphSnapshot:
    return build_knowledge_base_graph_snapshot(
        knowledge_base,
        identity=KnowledgeBaseIdentity(
            knowledge_base_module=knowledge_base.config.name,
            knowledge_base_version=knowledge_base.config.version,
        ),
    )


def _difference(
    row: models.Model, definition: ReferenceDefinition
) -> list[ReferenceDifference]:
    differences = [
        ReferenceDifference(field=name, existing=getattr(row, name), proposed=value)
        for name, value in definition.attributes.items()
        if (getattr(row, name) or "") != (value or "")
    ]
    for field, refs in definition.relations.items():
        manager = cast(ReferenceRelation, getattr(row, field))
        existing = list(manager.all().values_list("name", flat=True))
        if sorted(existing) != sorted(ref.name for ref in refs):
            differences.append(
                ReferenceDifference(
                    field=field,
                    existing=sorted(existing),
                    proposed=[name for name in sorted(ref.name for ref in refs)],
                )
            )
    # Legacy descriptor definitions cannot be silently erased or reinterpreted.
    for field in ("subcategories", "numerical_descriptors"):
        if getattr(row, field, None):
            differences.append(
                ReferenceDifference(
                    field=field, existing=getattr(row, field), proposed=None
                )
            )
    return differences


def plan_clinical_reference_import(
    snapshot: KnowledgeBaseGraphSnapshot, *, adopt_existing: bool = False
) -> ClinicalReferencePlan:
    definitions = compile_clinical_references(snapshot)
    module = snapshot.identity.knowledge_base_module
    version = snapshot.identity.knowledge_base_version
    receipts = list(ClinicalReferenceImport.objects.all())
    imported: dict[tuple[ReferenceModel, str], set[int]] = {}
    previous_meanings: dict[
        tuple[ReferenceModel, str], list[KnowledgeBaseGraphSnapshot]
    ] = {}
    existing_receipt: ClinicalReferenceReceipt | None = None
    conflicts: list[str] = []
    for receipt_row in receipts:
        receipt = ClinicalReferenceReceipt.model_validate(receipt_row.payload)
        if receipt_row.module == module and receipt_row.version == version:
            existing_receipt = receipt
            if receipt_row.snapshot_id != snapshot.snapshot_id:
                conflicts.append("Same module/version already has different content")
        for binding in receipt.bindings:
            imported.setdefault(binding.identity, set()).add(binding.row_id)
            previous_meanings.setdefault(binding.identity, []).append(receipt.snapshot)
    changes: list[ReferenceChange] = []
    bound = (
        {b.identity: b.row_id for b in existing_receipt.bindings}
        if existing_receipt
        else {}
    )
    for definition in definitions:
        candidates = list(
            MODEL_TYPES[definition.model]._default_manager.filter(name=definition.name)
        )
        action: Literal["create", "reuse", "adopt", "conflict"]
        row_id: int | None = None
        differences: list[str] = []
        details: list[ReferenceDifference] = []
        if len(candidates) > 1:
            action, differences = "conflict", ["ambiguous_name"]
        elif not candidates:
            action = "create"
            if definition.identity in imported:
                action, differences = "conflict", ["missing_imported_row"]
        else:
            row = candidates[0]
            row_id = cast(int, row.pk)
            details = _difference(row, definition)
            differences = [detail.field for detail in details]
            if definition.identity in bound and bound[definition.identity] != row_id:
                differences.append("row_identity_changed")
            known = row_id in imported.get(definition.identity, set())
            for previous_snapshot in previous_meanings.get(definition.identity, []):
                if (
                    previous_snapshot.snapshot_id != snapshot.snapshot_id
                    and _semantic_closure(previous_snapshot, definition)
                    != _semantic_closure(snapshot, definition)
                ):
                    differences.append("immutable_clinical_meaning")
                    break
            if differences:
                action = "conflict"
            elif known:
                action = "reuse"
            elif adopt_existing:
                action = "adopt"
            else:
                action, differences = "conflict", ["explicit_legacy_adoption_required"]
        changes.append(
            ReferenceChange(
                model=definition.model,
                name=definition.name,
                action=action,
                row_id=row_id,
                differing_fields=differences,
                differences=details,
            )
        )
    return ClinicalReferencePlan(
        module=module,
        version=version,
        snapshot_id=snapshot.snapshot_id,
        changes=changes,
        conflicts=conflicts,
        unchanged=existing_receipt is not None
        and not conflicts
        and all(c.action == "reuse" for c in changes),
    )


def import_clinical_references(
    snapshot: KnowledgeBaseGraphSnapshot, *, adopt_existing: bool = False
) -> ClinicalReferencePlan:
    snapshot = KnowledgeBaseGraphSnapshot.model_validate(snapshot.model_dump())
    definitions = compile_clinical_references(snapshot)
    # A global reference lock is required because different bundles share concepts.
    with (
        advisory_file_lock(
            lock_path=get_runtime_paths().terminology / ".host-preset-import.lock"
        ),
        transaction.atomic(),
    ):
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", [20260928, 1])
        plan = plan_clinical_reference_import(snapshot, adopt_existing=adopt_existing)
        if not plan.can_import:
            raise ValueError(
                "Clinical reference conflicts; inspect the dry-run plan before importing"
            )
        if plan.unchanged:
            emit_structured_event(
                logger,
                "clinical_reference.unchanged",
                module=plan.module,
                version=plan.version,
                snapshot_id=plan.snapshot_id,
            )
            return plan
        rows: dict[tuple[ReferenceModel, str], models.Model] = {}
        bindings: list[ReferenceBinding] = []
        for definition, change in zip(definitions, plan.changes, strict=True):
            manager = MODEL_TYPES[definition.model]._default_manager
            if change.action == "create":
                row = manager.create(name=definition.name, **definition.attributes)
            else:
                row = manager.get(pk=change.row_id)
            rows[definition.identity] = row
            bindings.append(
                ReferenceBinding(
                    model=definition.model,
                    name=definition.name,
                    row_id=cast(int, row.pk),
                    origin="created"
                    if change.action == "create"
                    else "legacy_equivalent"
                    if change.action == "adopt"
                    else "shared_import",
                )
            )
        for definition, change in zip(definitions, plan.changes, strict=True):
            if change.action != "create":
                continue
            row = rows[definition.identity]
            for field, refs in definition.relations.items():
                cast(ReferenceRelation, getattr(row, field)).set(
                    rows[ref.identity] for ref in refs
                )
        receipt = ClinicalReferenceReceipt(snapshot=snapshot, bindings=bindings)
        ClinicalReferenceImport.objects.create(
            module=plan.module,
            version=plan.version,
            snapshot_id=plan.snapshot_id,
            payload=cast(JsonObject, receipt.model_dump(mode="json")),
        )
        transaction.on_commit(
            lambda: emit_structured_event(
                logger,
                "clinical_reference.imported",
                module=plan.module,
                version=plan.version,
                snapshot_id=plan.snapshot_id,
                created=sum(c.action == "create" for c in plan.changes),
                adopted=sum(c.action == "adopt" for c in plan.changes),
                reused=sum(c.action == "reuse" for c in plan.changes),
            )
        )
        return plan
