"""Reviewed deployment plans and atomic reference/default-center provisioning."""

from __future__ import annotations

import logging
from typing import Literal

from django.db import connection, transaction
from lx_dtypes.models.contracts.deployment_setup import DeploymentLock, DeploymentSetup
from lx_dtypes.models.contracts.reference_catalog import (
    CatalogRecordBase,
    ReferenceKind,
)
from lx_dtypes.models.contracts.reference_catalog_snapshot import (
    ReferenceCatalogSnapshot,
)
from lx_dtypes.models.interface.KnowledgeBase import KnowledgeBase
from lx_dtypes.models.interface.KnowledgeBaseResolver import (
    clear_knowledge_base_resolver_caches,
)
from lx_dtypes.terminology.lookup_tracker import register_runtime_lookup_tracker
from lx_dtypes.terminology.terminology_service import TerminologyService
from lx_dtypes.utils.deployment_setup import ResolvedDeployment, resolve_deployment
from pydantic import BaseModel, ConfigDict, Field

from endoreg_db.models.administration.app_settings import ApplicationSettings
from endoreg_db.models.administration.center.center import Center
from endoreg_db.services.reference_data.clinical_projection import clinical_snapshot
from endoreg_db.services.reference_data.catalog import (
    CatalogPlan,
    catalog_snapshot,
    import_reference_catalog_in_transaction,
    plan_reference_catalog,
    reference_catalog_transaction,
)
from endoreg_db.utils.file_operations import advisory_file_lock
from endoreg_db.utils.paths import get_runtime_paths
from endoreg_db.utils.structured_logging import emit_structured_event

logger = logging.getLogger(__name__)


class DeploymentPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    lock: DeploymentLock
    site_action: Literal["create", "reuse", "conflict"]
    default_center_action: Literal["select", "reuse"]
    reference_plans: list[CatalogPlan]
    conflicts: list[str] = Field(default_factory=list)
    can_apply: bool
    activation: Literal["unchanged", "select"]


class DeploymentActivationError(RuntimeError):
    """Database commit succeeded; registry activation must be inspected/retried."""


def _catalogues(resolved: ResolvedDeployment) -> list[ReferenceCatalogSnapshot]:
    return [
        catalog_snapshot(resolved.knowledge_bases[p.module])
        for p in resolved.lock.setup.reference_packages
    ]


def validate_deployment(
    setup: DeploymentSetup, service: TerminologyService
) -> ResolvedDeployment:
    resolved = resolve_deployment(setup, service)
    # All catalogue edges and all selected clinical graphs validate before writes.
    _catalogues(resolved)
    for package in setup.study_packages:
        clinical_snapshot(resolved.knowledge_bases[package.module])
    return resolved


def plan_deployment(resolved: ResolvedDeployment) -> DeploymentPlan:
    setup = resolved.lock.setup
    conflicts: list[str] = []
    records: dict[tuple[ReferenceKind, str, int | None], CatalogRecordBase] = {}
    snapshots = _catalogues(resolved)
    for snapshot in snapshots:
        for record in snapshot.payload.records:
            previous = records.get(record.key)
            if previous is not None and previous != record:
                conflicts.append(
                    f"incompatible package definitions: {record.kind}/{record.identity.name}"
                )
            records[record.key] = record
            if record.kind == ReferenceKind.CENTER:
                fields = record.model_dump()
                if (
                    fields.get("center_key") == setup.site.center_key
                    and fields.get("name") != setup.site.name
                ):
                    conflicts.append(
                        "site conflicts with a reference package center name"
                    )
                if (
                    fields.get("name") == setup.site.name
                    and fields.get("center_key") != setup.site.center_key
                ):
                    conflicts.append(
                        "site name belongs to a different package center key"
                    )
    center = Center.objects.filter(center_key=setup.site.center_key).first()
    site_action: Literal["create", "reuse", "conflict"] = (
        "create" if center is None else "reuse"
    )
    if center is not None and center.name != setup.site.name:
        conflicts.append("existing center_key has a different name")
        site_action = "conflict"
    if (
        Center.objects.filter(name=setup.site.name)
        .exclude(center_key=setup.site.center_key)
        .exists()
    ):
        conflicts.append("site name has an ambiguous or different center identity")
        site_action = "conflict"
    selected = ApplicationSettings.objects.filter(pk=1).first()
    default_action: Literal["select", "reuse"] = (
        "reuse"
        if center is not None and selected is not None and selected.center == center
        else "select"
    )
    plans = [
        plan_reference_catalog(snapshot, adopt_existing=setup.adopt_existing)
        for snapshot in snapshots
    ]
    return DeploymentPlan(
        lock=resolved.lock,
        site_action=site_action,
        default_center_action=default_action,
        reference_plans=plans,
        conflicts=conflicts,
        can_apply=not conflicts and all(p.can_import for p in plans),
        activation="select"
        if any(p.activate for p in setup.study_packages)
        else "unchanged",
    )


def apply_deployment(
    setup: DeploymentSetup, expected: DeploymentLock, service: TerminologyService
) -> DeploymentPlan:
    # Refuse an outer transaction: activation must never precede the real commit.
    if connection.in_atomic_block:
        raise ValueError(
            "Deployment apply requires its own outermost database transaction"
        )
    setup = DeploymentSetup.model_validate_json(setup.model_dump_json())
    expected = DeploymentLock.model_validate_json(expected.model_dump_json())
    if expected.setup != setup:
        raise ValueError("manifest differs from the reviewed lock; run plan again")
    with advisory_file_lock(
        lock_path=get_runtime_paths().locks / "deployment-setup.lock"
    ):
        resolved = validate_deployment(setup, service)
        if resolved.lock != expected:
            raise ValueError(
                "package sources differ from the reviewed lock; run plan again"
            )
        activating = next((p for p in setup.study_packages if p.activate), None)
        registry_revision = (
            service.list_bundles().revision if activating is not None else None
        )
        # The local-center lock also coordinates first intake; catalogue and preset
        # writers use the shared reference lock. Fixed lock order prevents cycles.
        with (
            advisory_file_lock(
                lock_path=get_runtime_paths().locks / "local-center.lock"
            ),
            reference_catalog_transaction(),
        ):
            plan = plan_deployment(resolved)
            if not plan.can_apply:
                raise ValueError("deployment conflicts; inspect setup_deployment plan")
            for snapshot in _catalogues(resolved):
                import_reference_catalog_in_transaction(
                    snapshot, adopt_existing=setup.adopt_existing
                )
            center, _ = Center.objects.get_or_create(
                center_key=setup.site.center_key, defaults={"name": setup.site.name}
            )
            if center.name != setup.site.name:
                raise ValueError("center identity changed during deployment")
            settings_row, _ = ApplicationSettings.objects.get_or_create(pk=1)
            if settings_row.center != center:
                settings_row.center = center
                settings_row.save(update_fields=["center", "updated_at"])
            transaction.on_commit(
                lambda: emit_structured_event(
                    logger,
                    "deployment_setup.database_committed",
                    center_key=setup.site.center_key,
                    packages=len(resolved.lock.packages),
                )
            )
        if activating is not None and registry_revision is not None:

            def load_selected() -> KnowledgeBase:
                fresh = validate_deployment(setup, service)
                if fresh.lock != expected:
                    raise ValueError("package sources changed before activation")
                return fresh.knowledge_bases[activating.module]

            try:
                service.select(
                    activating.module,
                    activating.version,
                    expected_revision=registry_revision,
                    on_selected=register_runtime_lookup_tracker,
                    clear_application_caches=clear_knowledge_base_resolver_caches,
                    load_selected=load_selected,
                )
            except Exception as exc:
                # Explicit cross-store integration boundary: never imply DB rollback.
                raise DeploymentActivationError(
                    "Database setup committed; registry activation was not confirmed. Inspect active terminology and retry the same locked setup."
                ) from exc
        return plan
