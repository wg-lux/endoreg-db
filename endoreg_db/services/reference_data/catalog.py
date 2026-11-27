"""Transactional projection of the complete, versioned reference catalogue."""

import logging
from collections.abc import Generator
from contextlib import contextmanager
from typing import Literal, cast

from django.db import connection, models, transaction
from lx_dtypes.models.contracts.knowledge_base import KnowledgeBaseIdentity
from lx_dtypes.models.contracts.reference_catalog import (
    CatalogIdentity,
    CatalogRecordBase,
    ReferenceCatalogPayload,
    ReferenceKind,
    TERMINOLOGY_REFERENCE_MODELS,
)
from lx_dtypes.models.contracts.reference_catalog_snapshot import (
    CatalogBinding,
    ReferenceCatalogReceipt,
    ReferenceCatalogSnapshot,
)
from lx_dtypes.models.contracts.reference_migration import (
    DISPLAY_FIELDS,
    ReferenceFieldDecision,
    ReferenceRowMigration,
    ReferenceRowState,
    load_reference_migrations,
    plan_reference_row_migration,
)
from lx_dtypes.models.interface.KnowledgeBase import KnowledgeBase
from lx_dtypes.models.contracts.package_snapshot import (
    PackageSnapshot,
    build_package_snapshot,
    restore_package_snapshot,
)
from endoreg_db.services.reference_data.clinical_projection import (
    clinical_snapshot,
    import_clinical_references_in_transaction,
    plan_clinical_reference_import,
    reuse_clinical_package,
)
from endoreg_db.schemas.clinical_reference import ClinicalReferencePlan

from pydantic import BaseModel, ConfigDict, Field

from endoreg_db.models.other.reference_catalog_import import ReferenceCatalogImport
from endoreg_db.services.reference_data.catalog_export import export_reference_row
from endoreg_db.services.reference_data.catalog_models import CATALOG_MODELS
from endoreg_db.utils.file_operations import advisory_file_lock
from endoreg_db.utils.paths import get_runtime_paths
from endoreg_db.utils.structured_logging import emit_structured_event

from endoreg_db.helpers.typing import ReferenceRelation

logger = logging.getLogger(__name__)


class CatalogChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: ReferenceKind
    identity: CatalogIdentity
    # update_display_name: an already bound row whose only difference is a
    # display field; apply rewrites just those fields.
    action: Literal[
        "create", "adopt", "reuse", "update_display_name", "migrate", "conflict"
    ]
    row_id: int | None = None
    differing_fields: list[str] = Field(default_factory=list)
    # Kept in the plan output for clients that display label changes.
    display_fields: list[str] = Field(default_factory=list)
    migration: ReferenceRowMigration | None = None


class CatalogPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    module: str
    version: str
    snapshot_id: str
    projection: str
    changes: list[CatalogChange]
    conflicts: list[str] = Field(default_factory=list)
    clinical_plan: ClinicalReferencePlan | None = None
    applied_migrations: list[ReferenceRowMigration] = Field(default_factory=list)
    unchanged: bool = False

    @property
    def can_import(self) -> bool:
        return not self.conflicts and all(
            item.action != "conflict" for item in self.changes
        )


def catalog_snapshot(knowledge_base: KnowledgeBase) -> ReferenceCatalogSnapshot:
    records = [
        record.model_dump(mode="json", by_alias=True)
        for catalog in knowledge_base.reference_catalog.values()
        for record in catalog.payload.records
    ]
    payload = ReferenceCatalogPayload.model_validate({"records": records})
    identity = KnowledgeBaseIdentity(
        knowledge_base_module=knowledge_base.config.name,
        knowledge_base_version=knowledge_base.config.version,
    )
    clinical_package = (
        PackageSnapshot.model_validate_json(
            build_package_snapshot(knowledge_base).model_dump_json()
        )
        if knowledge_base.config.depends_on
        and not any(
            record.kind in TERMINOLOGY_REFERENCE_MODELS for record in payload.records
        )
        and any(
            getattr(knowledge_base, family).keys()
            for family in ("examination", "finding", "unit")
        )
        else None
    )
    return ReferenceCatalogSnapshot(
        identity=identity,
        payload=payload,
        clinical_package=clinical_package,
        snapshot_id=ReferenceCatalogSnapshot.content_digest(
            identity, payload, clinical_package=clinical_package
        ),
    )


def _lookup(record: CatalogRecordBase) -> dict[str, object]:
    name: object = getattr(record, "name", None)
    if name is not None:
        result: dict[str, object] = {"name": name}
        if record.identity.version is not None:
            result["version"] = record.identity.version
        return result
    result = {}
    for field in record.identity_fields:
        value: object = getattr(record, field)
        if isinstance(value, CatalogIdentity):
            result[f"{field}__name"] = value.name
            if value.version is not None:
                result[f"{field}__version"] = value.version
        else:
            result[field] = value
    if not result:
        raise ValueError("Missing reference lookup identity")
    return result


def select_catalog(
    snapshot: ReferenceCatalogSnapshot, kinds: set[ReferenceKind]
) -> ReferenceCatalogSnapshot:
    """Select explicit record types and their complete reference dependency closure."""
    if not kinds:
        return snapshot
    if snapshot.clinical_package is not None and kinds.intersection(
        TERMINOLOGY_REFERENCE_MODELS
    ):
        # Clinical concepts are owned by the pinned graph, not catalogue rows.
        # Import its complete dependency closure through the same atomic boundary.
        for kind in kinds.intersection(TERMINOLOGY_REFERENCE_MODELS):
            family = TERMINOLOGY_REFERENCE_MODELS[kind]
            if not snapshot.clinical_package.records[family]:
                raise ValueError(f"Selected package has no {kind.value} definitions")
        return snapshot
    records = {record.key: record for record in snapshot.payload.records}
    selected = {key for key in records if key[0] in kinds}
    pending = list(selected)
    while pending:
        for _, kind, identity in records[pending.pop()].references():
            key = (kind, identity.name, identity.version)
            if key in records and key not in selected:
                selected.add(key)
                pending.append(key)
    payload = ReferenceCatalogPayload.model_validate(
        {
            "records": [
                record.model_dump(mode="json", by_alias=True)
                for record in snapshot.payload.records
                if record.key in selected
            ]
        }
    )
    projection = ",".join(sorted(kind.value for kind in kinds))
    clinical_package = (
        snapshot.clinical_package
        if any(
            kind in TERMINOLOGY_REFERENCE_MODELS
            for record in payload.records
            for _, kind, _ in record.references()
        )
        else None
    )
    return ReferenceCatalogSnapshot(
        identity=snapshot.identity,
        payload=payload,
        projection=projection,
        clinical_package=clinical_package,
        snapshot_id=ReferenceCatalogSnapshot.content_digest(
            snapshot.identity, payload, projection, clinical_package
        ),
    )


def plan_reference_catalog(
    snapshot: ReferenceCatalogSnapshot, *, adopt_existing: bool = False
) -> CatalogPlan:
    # Revalidate mutable nested models at this boundary, before reading or writing.
    snapshot = ReferenceCatalogSnapshot.model_validate_json(
        snapshot.model_dump_json(by_alias=True)
    )
    module, version = (
        snapshot.identity.knowledge_base_module,
        snapshot.identity.knowledge_base_version,
    )
    target = load_reference_migrations().target(module, version)
    receipts = [
        ReferenceCatalogReceipt.model_validate(row.payload)
        for row in ReferenceCatalogImport.objects.order_by("pk")
    ]
    previous = next(
        (
            receipt
            for receipt in receipts
            if receipt.snapshot.identity == snapshot.identity
            and receipt.snapshot.projection == snapshot.projection
        ),
        None,
    )
    clinical_plan = None
    if snapshot.clinical_package is not None:
        clinical_plan = plan_clinical_reference_import(
            clinical_snapshot(restore_package_snapshot(snapshot.clinical_package)),
            adopt_existing=adopt_existing,
            package_snapshot=snapshot.clinical_package,
            migration_target=target,
        )
    conflicts: list[str] = []
    if clinical_plan is not None and not clinical_plan.can_import:
        conflicts.append("Terminology dependencies conflict; review the clinical plan")
    if previous and previous.snapshot.snapshot_id != snapshot.snapshot_id:
        conflicts.append("Same module/version already has different content")
    known = {
        (binding.kind, binding.identity.name, binding.identity.version, binding.row_id)
        for receipt in receipts
        for binding in receipt.bindings
    }
    installed = {
        (binding.kind, binding.identity.name, binding.identity.version)
        for receipt in receipts
        if receipt.snapshot.identity == snapshot.identity
        for binding in receipt.bindings
    }
    meanings: dict[tuple[ReferenceKind, str, int | None], CatalogRecordBase] = {}
    # Compare against the most recent successful projection. Earlier receipts
    # still describe the versions that were in use before a reviewed correction.
    for receipt in receipts:
        for record in receipt.snapshot.payload.records:
            meanings[record.key] = record
    bound = (
        {
            (
                binding.kind,
                binding.identity.name,
                binding.identity.version,
            ): binding.row_id
            for binding in previous.bindings
        }
        if previous
        else {}
    )
    changes: list[CatalogChange] = []
    for record in snapshot.payload.records:
        candidates = list(
            CATALOG_MODELS[record.kind]._base_manager.filter(**_lookup(record))
        )
        differences: list[str] = []
        display_fields: list[str] = []
        migration: ReferenceRowMigration | None = None
        row_id: int | None = None
        action: Literal[
            "create", "adopt", "reuse", "update_display_name", "migrate", "conflict"
        ] = "create"
        if len(candidates) > 1:
            differences.append("ambiguous_identity")
        elif not candidates:
            if any(key[:3] == record.key for key in known):
                differences.append("missing_imported_row")
        else:
            row = candidates[0]
            row_id = cast(int, row.pk)
            actual = export_reference_row(record.kind, row)
            proposed = _row_state(record)
            classified = plan_reference_row_migration(
                _row_state(actual), proposed, target
            )
            blocked_fields = {
                "record_type",
                *record.identity_fields,
                *record.relation_targets,
                *record.many_relations,
            }
            for change in classified.changes:
                field = change.field
                if field in DISPLAY_FIELDS and record.key not in installed:
                    display_fields.append(field)
                if (
                    change.decision == ReferenceFieldDecision.CONFLICT
                    or field in blocked_fields
                    or record.key in installed
                    or (
                        record.kind in TERMINOLOGY_REFERENCE_MODELS
                        and field not in DISPLAY_FIELDS
                    )
                ):
                    differences.append(field)
            old = meanings.get(record.key)
            if old is not None:
                history = plan_reference_row_migration(
                    _row_state(old), proposed, target
                )
                if any(
                    change.field not in DISPLAY_FIELDS
                    and (
                        change.decision == ReferenceFieldDecision.CONFLICT
                        or change.field in blocked_fields
                        or record.kind in TERMINOLOGY_REFERENCE_MODELS
                    )
                    for change in history.changes
                ):
                    differences.append("immutable_clinical_meaning")
            if record.key in bound and bound[record.key] != row_id:
                differences.append("row_identity_changed")
            if (*record.key, row_id) in known:
                action = "reuse"
                if classified.changes:
                    action = (
                        "update_display_name"
                        if len(display_fields) == len(classified.changes)
                        else "migrate"
                    )
            elif adopt_existing:
                action = "adopt"
            else:
                differences.append("explicit_legacy_adoption_required")
            if classified.changes and not differences:
                migration = classified
        if differences:
            action = "conflict"
            display_fields = []
        changes.append(
            CatalogChange(
                kind=record.kind,
                identity=record.identity,
                action=action,
                row_id=row_id,
                differing_fields=differences,
                display_fields=display_fields,
                migration=migration,
            )
        )
    return CatalogPlan(
        module=module,
        version=version,
        snapshot_id=snapshot.snapshot_id,
        projection=snapshot.projection,
        changes=changes,
        conflicts=conflicts,
        clinical_plan=clinical_plan,
        applied_migrations=previous.migrations if previous else [],
        unchanged=previous is not None
        and (clinical_plan is None or clinical_plan.unchanged)
        and not conflicts
        and all(change.action == "reuse" for change in changes),
    )


def _row_state(record: CatalogRecordBase) -> ReferenceRowState:
    return ReferenceRowState(
        kind=record.kind,
        identity=record.identity,
        fields=record.model_dump(mode="json", by_alias=True),
    )


def reuse_current_catalog(snapshot: ReferenceCatalogSnapshot) -> CatalogPlan | None:
    """Return the receipt-backed plan when this exact snapshot is already projected.

    The stored receipt must carry the same content digest for the same module,
    version and projection, and every row it binds must still exist. Any other
    state returns None so callers run the full field-level reconciliation, which
    also reports content changes for an already imported version as conflicts.
    """
    if not connection.in_atomic_block:
        raise RuntimeError("Catalogue reuse check requires the reference transaction")
    snapshot = ReferenceCatalogSnapshot.model_validate_json(
        snapshot.model_dump_json(by_alias=True)
    )
    clinical_plan = None
    if snapshot.clinical_package is not None:
        clinical_plan = reuse_clinical_package(snapshot.clinical_package)
        if clinical_plan is None:
            return None
    row = ReferenceCatalogImport.objects.filter(
        module=snapshot.identity.knowledge_base_module,
        version=snapshot.identity.knowledge_base_version,
        projection=snapshot.projection,
    ).first()
    if row is None or row.snapshot_id != snapshot.snapshot_id:
        return None
    receipt = ReferenceCatalogReceipt.model_validate(row.payload)
    if (
        receipt.snapshot.identity != snapshot.identity
        or receipt.snapshot.projection != snapshot.projection
        or receipt.snapshot.snapshot_id != snapshot.snapshot_id
    ):
        raise ValueError("Reference catalogue receipt does not match its digest")
    bound: dict[ReferenceKind, set[int]] = {}
    for binding in receipt.bindings:
        bound.setdefault(binding.kind, set()).add(binding.row_id)
    for kind, row_ids in bound.items():
        present = CATALOG_MODELS[kind]._base_manager.filter(pk__in=row_ids).count()
        if present != len(row_ids):
            return None
    emit_structured_event(
        logger,
        "reference_catalog.reused",
        module=row.module,
        version=row.version,
        snapshot_id=row.snapshot_id,
        projection=row.projection,
        records=len(receipt.bindings),
    )
    return CatalogPlan(
        module=row.module,
        version=row.version,
        snapshot_id=row.snapshot_id,
        projection=row.projection,
        changes=[
            CatalogChange(
                kind=binding.kind,
                identity=binding.identity,
                action="reuse",
                row_id=binding.row_id,
            )
            for binding in receipt.bindings
        ],
        clinical_plan=clinical_plan,
        applied_migrations=receipt.migrations,
        unchanged=True,
    )


@contextmanager
def reference_catalog_transaction() -> Generator[None]:
    """Serialize all cooperating reference writers around one database transaction."""
    with (
        advisory_file_lock(
            lock_path=get_runtime_paths().terminology / ".host-preset-import.lock"
        ),
        transaction.atomic(),
    ):
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", [20260928, 1])
        yield


def import_reference_catalog(
    snapshot: ReferenceCatalogSnapshot, *, adopt_existing: bool = False
) -> CatalogPlan:
    with reference_catalog_transaction():
        return import_reference_catalog_in_transaction(
            snapshot, adopt_existing=adopt_existing
        )


def import_reference_catalog_in_transaction(
    snapshot: ReferenceCatalogSnapshot, *, adopt_existing: bool = False
) -> CatalogPlan:
    """Compose catalogue imports under reference_catalog_transaction's outer lock."""
    if not connection.in_atomic_block:
        raise RuntimeError("Catalogue import requires the reference transaction")
    snapshot = ReferenceCatalogSnapshot.model_validate_json(
        snapshot.model_dump_json(by_alias=True)
    )
    plan = plan_reference_catalog(snapshot, adopt_existing=adopt_existing)
    if not plan.can_import:
        raise ValueError("Reference catalogue conflicts; inspect the dry-run plan")
    if plan.unchanged:
        return plan
    rows: dict[tuple[ReferenceKind, str, int | None], models.Model] = {}
    clinical_plan = plan.clinical_plan
    if snapshot.clinical_package is not None:
        clinical_plan = import_clinical_references_in_transaction(
            clinical_snapshot(restore_package_snapshot(snapshot.clinical_package)),
            adopt_existing=adopt_existing,
            package_snapshot=snapshot.clinical_package,
            migration_target=load_reference_migrations().target(
                plan.module, plan.version
            ),
        )
        for kind, family in TERMINOLOGY_REFERENCE_MODELS.items():
            for record in snapshot.clinical_package.records[family]:
                # Only actual external catalogue links need host rows. Some KB
                # families have a more selective host projection (indications).
                key = (kind, record.name, None)
                if any(
                    (target, identity.name, identity.version) == key
                    for item in snapshot.payload.records
                    for _, target, identity in item.references()
                ):
                    rows[key] = CATALOG_MODELS[kind]._base_manager.get(name=record.name)
    pending: list[tuple[CatalogRecordBase, CatalogChange]] = []
    for record, change in zip(snapshot.payload.records, plan.changes, strict=True):
        if change.action == "create":
            pending.append((record, change))
        else:
            row = CATALOG_MODELS[record.kind]._base_manager.get(pk=change.row_id)
            if change.migration is not None:
                fields = list(change.migration.updates)
                values = record.model_dump(mode="python", by_alias=True)
                for field in fields:
                    setattr(row, field, values[field])
                row.save(update_fields=fields)
            rows[record.key] = row
    while pending:
        remaining: list[tuple[CatalogRecordBase, CatalogChange]] = []
        for record, change in pending:
            references = [
                (field, (kind, identity.name, identity.version))
                for field, kind, identity in record.references()
                if field not in record.many_relations
            ]
            if any(key not in rows for _, key in references):
                remaining.append((record, change))
                continue
            values = record.model_dump(
                mode="python",
                by_alias=True,
                exclude={"record_type", *record.many_relations},
            )
            # Optional descriptor keys use absence in the existing JSON boundary.
            for field in ("subcategories", "numerical_descriptors"):
                if field in values:
                    values[field] = record.model_dump(
                        mode="json", by_alias=True, exclude_none=True
                    )[field]
            for field, key in references:
                values[field] = rows[key]
            rows[record.key] = CATALOG_MODELS[record.kind]._base_manager.create(
                **values
            )
        if len(remaining) == len(pending):
            raise ValueError(
                "Catalogue contains an unsupported mandatory foreign-key cycle"
            )
        pending = remaining
    for record, change in zip(snapshot.payload.records, plan.changes, strict=True):
        if change.action != "create":
            continue
        for field in record.many_relations:
            manager = cast(ReferenceRelation, getattr(rows[record.key], field))
            related = [
                rows[(kind, identity.name, identity.version)]
                for name, kind, identity in record.references()
                if name == field
            ]
            manager.set(related)
    receipt = ReferenceCatalogReceipt(
        snapshot=snapshot,
        migrations=[
            change.migration for change in plan.changes if change.migration is not None
        ],
        bindings=[
            CatalogBinding(
                kind=record.kind,
                identity=record.identity,
                row_id=cast(int, rows[record.key].pk),
                origin="created"
                if change.action == "create"
                else "legacy_equivalent"
                if change.action == "adopt"
                else "shared_import",
            )
            for record, change in zip(
                snapshot.payload.records, plan.changes, strict=True
            )
        ],
    )
    ReferenceCatalogImport.objects.create(
        module=plan.module,
        version=plan.version,
        snapshot_id=plan.snapshot_id,
        projection=plan.projection,
        payload=receipt.model_dump(mode="json", by_alias=True),
    )
    transaction.on_commit(
        lambda: emit_structured_event(
            logger,
            "reference_catalog.imported",
            module=plan.module,
            version=plan.version,
            snapshot_id=plan.snapshot_id,
            records=len(plan.changes),
        )
    )
    return plan.model_copy(
        update={
            "applied_migrations": receipt.migrations,
            "clinical_plan": clinical_plan,
        }
    )
