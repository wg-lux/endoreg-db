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
)
from lx_dtypes.models.contracts.reference_catalog_snapshot import (
    CatalogBinding,
    ReferenceCatalogReceipt,
    ReferenceCatalogSnapshot,
)
from lx_dtypes.models.interface.KnowledgeBase import KnowledgeBase
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
    action: Literal["create", "adopt", "reuse", "conflict"]
    row_id: int | None = None
    differing_fields: list[str] = Field(default_factory=list)


class CatalogPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    module: str
    version: str
    snapshot_id: str
    projection: str
    changes: list[CatalogChange]
    conflicts: list[str] = Field(default_factory=list)
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
    return ReferenceCatalogSnapshot(
        identity=identity,
        payload=payload,
        snapshot_id=ReferenceCatalogSnapshot.content_digest(identity, payload),
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
    records = {record.key: record for record in snapshot.payload.records}
    selected = {key for key in records if key[0] in kinds}
    pending = list(selected)
    while pending:
        for _, kind, identity in records[pending.pop()].references():
            key = (kind, identity.name, identity.version)
            if key not in selected:
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
    return ReferenceCatalogSnapshot(
        identity=snapshot.identity,
        payload=payload,
        projection=projection,
        snapshot_id=ReferenceCatalogSnapshot.content_digest(
            snapshot.identity, payload, projection
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
    receipts = [
        ReferenceCatalogReceipt.model_validate(row.payload)
        for row in ReferenceCatalogImport.objects.all()
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
    conflicts: list[str] = []
    if previous and previous.snapshot != snapshot:
        conflicts.append("Same module/version already has different content")
    known = {
        (binding.kind, binding.identity.name, binding.identity.version, binding.row_id)
        for receipt in receipts
        for binding in receipt.bindings
    }
    meanings: dict[tuple[ReferenceKind, str, int | None], list[CatalogRecordBase]] = {}
    for receipt in receipts:
        for record in receipt.snapshot.payload.records:
            meanings.setdefault(record.key, []).append(record)
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
        row_id: int | None = None
        action: Literal["create", "adopt", "reuse", "conflict"] = "create"
        if len(candidates) > 1:
            differences.append("ambiguous_identity")
        elif not candidates:
            if any(key[:3] == record.key for key in known):
                differences.append("missing_imported_row")
        else:
            row = candidates[0]
            row_id = cast(int, row.pk)
            actual = export_reference_row(record.kind, row)
            actual_fields = actual.model_dump(mode="json", by_alias=True)
            expected_fields = record.model_dump(mode="json", by_alias=True)
            differences.extend(
                field
                for field in expected_fields
                if actual_fields[field] != expected_fields[field]
            )
            if any(
                old.model_dump(mode="json", by_alias=True) != expected_fields
                for old in meanings.get(record.key, [])
            ):
                differences.append("immutable_clinical_meaning")
            if record.key in bound and bound[record.key] != row_id:
                differences.append("row_identity_changed")
            if (*record.key, row_id) in known:
                action = "reuse"
            elif adopt_existing:
                action = "adopt"
            else:
                differences.append("explicit_legacy_adoption_required")
        if differences:
            action = "conflict"
        changes.append(
            CatalogChange(
                kind=record.kind,
                identity=record.identity,
                action=action,
                row_id=row_id,
                differing_fields=differences,
            )
        )
    return CatalogPlan(
        module=module,
        version=version,
        snapshot_id=snapshot.snapshot_id,
        projection=snapshot.projection,
        changes=changes,
        conflicts=conflicts,
        unchanged=previous is not None
        and not conflicts
        and all(change.action == "reuse" for change in changes),
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
    pending: list[tuple[CatalogRecordBase, CatalogChange]] = []
    for record, change in zip(snapshot.payload.records, plan.changes, strict=True):
        if change.action == "create":
            pending.append((record, change))
        else:
            rows[record.key] = CATALOG_MODELS[record.kind]._base_manager.get(
                pk=change.row_id
            )
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
    return plan
