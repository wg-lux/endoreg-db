"""One-time atomic reconciliation of legacy colorectal indication links."""

from __future__ import annotations

import logging
from importlib.resources import files
from typing import cast

from django.db import transaction
from lx_dtypes.models.contracts.reference_catalog import (
    ExaminationIndicationReference,
    ReferenceKind,
)
from lx_dtypes.models.contracts.reference_catalog_snapshot import (
    ReferenceCatalogReceipt,
    ReferenceCatalogSnapshot,
)
from lx_dtypes.utils.study_setup_yaml import parse_setup_yaml

from endoreg_db.helpers.typing import ReferenceRelation
from endoreg_db.models.other.reference_catalog_import import ReferenceCatalogImport
from endoreg_db.schemas.reference_catalog_migration import (
    IndicationRelationshipChange,
    MigrationPlan,
    MigrationSpec,
)
from endoreg_db.services.reference_data.catalog import (
    CatalogPlan,
    import_reference_catalog_in_transaction,
    plan_reference_catalog,
    reference_catalog_transaction,
    select_catalog,
)
from endoreg_db.services.reference_data.catalog_export import export_reference_row
from endoreg_db.services.reference_data.catalog_models import CATALOG_MODELS
from endoreg_db.utils.structured_logging import emit_structured_event

logger = logging.getLogger(__name__)


def migration_spec() -> MigrationSpec:
    source = files("endoreg_db").joinpath(
        "data_migrations/colorectal_indication_interventions_v1.yml"
    )
    return MigrationSpec.model_validate(
        parse_setup_yaml(source.read_text(encoding="utf-8"))
    )


def plan_catalog_migration(snapshot: ReferenceCatalogSnapshot) -> MigrationPlan:
    spec = migration_spec()
    if (
        snapshot.identity.knowledge_base_module != spec.module
        or snapshot.identity.knowledge_base_version != spec.version
        or snapshot.snapshot_id != spec.snapshot_id
        or snapshot.projection != "all"
    ):
        raise ValueError(
            "Migration requires the pinned endoreg_reference@1.0.0 content digest"
        )
    plan = plan_reference_catalog(snapshot, adopt_existing=True)
    # Clean installations and equivalent legacy catalogues use the normal importer.
    if plan.can_import:
        return MigrationPlan(reconciliation=plan, relationships=[], blockers=[])
    blockers = list(plan.conflicts)
    imported_indications = {
        binding.row_id
        for receipt in ReferenceCatalogImport.objects.all()
        for binding in ReferenceCatalogReceipt.model_validate(receipt.payload).bindings
        if binding.kind == ReferenceKind.EXAMINATION_INDICATION
    }
    allowed_creates = {
        (ReferenceKind.FINDING_INTERVENTION, name) for name in spec.interventions
    } | {
        (ReferenceKind.FINDING_INTERVENTION_TYPE, name)
        for name in spec.intervention_types
    }
    expected = {record.key: record for record in snapshot.payload.records}
    relationships: list[IndicationRelationshipChange] = []
    for change in plan.changes:
        label = f"{change.kind.value}:{change.identity.name}"
        if (
            change.action == "create"
            and (change.kind, change.identity.name) not in allowed_creates
        ):
            blockers.append(f"Unexpected missing record: {label}")
        if change.action != "conflict":
            continue
        if (
            change.kind != ReferenceKind.EXAMINATION_INDICATION
            or change.identity.name not in spec.indications
            or change.differing_fields != ["expected_interventions"]
            or change.row_id is None
        ):
            blockers.append(
                f"Unexpected conflict: {label} ({', '.join(change.differing_fields)})"
            )
            continue
        row = CATALOG_MODELS[change.kind]._base_manager.get(pk=change.row_id)
        if change.row_id in imported_indications:
            blockers.append(f"Previously imported indication drift: {label}")
            continue
        before = export_reference_row(change.kind, row)
        after = expected[(change.kind, change.identity.name, change.identity.version)]
        if not isinstance(before, ExaminationIndicationReference) or not isinstance(
            after, ExaminationIndicationReference
        ):
            raise TypeError("Expected typed examination indication records")
        old_links, new_links = (
            set(before.expected_interventions),
            set(after.expected_interventions),
        )
        additions = new_links - old_links
        if old_links - new_links or any(
            link.name not in spec.interventions or link.version is not None
            for link in additions
        ):
            blockers.append(f"Unexpected relationship change: {label}")
            continue
        relationships.append(
            IndicationRelationshipChange(
                row_id=change.row_id, before=before, after=after
            )
        )
    return MigrationPlan(
        reconciliation=plan,
        relationships=relationships,
        blockers=blockers,
    )


def apply_catalog_migration(snapshot: ReferenceCatalogSnapshot) -> CatalogPlan:
    spec = migration_spec()
    with reference_catalog_transaction():
        initial = plan_catalog_migration(snapshot)
        # Lock existing catalogue rows as well as the shared cooperating-writer lock.
        for kind, model in CATALOG_MODELS.items():
            row_ids = [
                c.row_id
                for c in initial.reconciliation.changes
                if c.kind == kind and c.row_id is not None
            ]
            if row_ids:
                list(
                    model._base_manager.select_for_update()
                    .filter(pk__in=row_ids)
                    .order_by("pk")
                )
        current = plan_catalog_migration(snapshot)
        if not current.can_apply:
            raise ValueError(
                "Migration contains blockers: " + "; ".join(current.blockers)
            )
        if current.reconciliation.unchanged:
            return current.reconciliation
        if current.reconciliation.can_import:
            import_reference_catalog_in_transaction(snapshot, adopt_existing=True)
            result = plan_reference_catalog(snapshot)
            if not result.unchanged:
                raise ValueError("Post-migration reconciliation failed")
            return result
        dependencies = select_catalog(
            snapshot,
            {
                ReferenceKind.FINDING_INTERVENTION,
                ReferenceKind.FINDING_INTERVENTION_TYPE,
            },
        )
        import_reference_catalog_in_transaction(dependencies, adopt_existing=True)
        indication_model = CATALOG_MODELS[ReferenceKind.EXAMINATION_INDICATION]
        intervention_model = CATALOG_MODELS[ReferenceKind.FINDING_INTERVENTION]
        for change in current.relationships:
            row = indication_model._base_manager.get(pk=change.row_id)
            manager = cast(ReferenceRelation, getattr(row, "expected_interventions"))
            related = list(
                intervention_model._base_manager.filter(
                    name__in=[
                        identity.name
                        for identity in change.after.expected_interventions
                    ]
                )
            )
            if len(related) != len(change.after.expected_interventions):
                raise ValueError(
                    "Migration intervention dependencies are missing or ambiguous"
                )
            manager.set(related)
        import_reference_catalog_in_transaction(snapshot, adopt_existing=True)
        result = plan_reference_catalog(snapshot)
        if not result.unchanged:
            raise ValueError("Post-migration reconciliation failed")
        transaction.on_commit(
            lambda: emit_structured_event(
                logger,
                "reference_catalog.migrated",
                migration_id=spec.migration_id,
                snapshot_id=snapshot.snapshot_id,
                relationships=[
                    change.model_dump(mode="json") for change in current.relationships
                ],
                created=[
                    change.model_dump(mode="json")
                    for change in current.reconciliation.changes
                    if change.action == "create"
                ],
            )
        )
        return result
