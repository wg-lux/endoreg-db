from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from importlib.resources import files
from io import StringIO
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest
import yaml
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connections
from lx_dtypes.models.contracts.deployment_setup import DeploymentSetup
from lx_dtypes.terminology.terminology_service import (
    TerminologyError,
    TerminologyService,
)
from lx_dtypes.utils.deployment_setup import parse_deployment_setup, resolve_deployment

from endoreg_db.models.administration.app_settings import ApplicationSettings
from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.other.reference_catalog_import import ReferenceCatalogImport
from endoreg_db.services.runtime.deployment_setup import (
    DeploymentActivationError,
    DeploymentPackageKindError,
    apply_deployment,
    migrate_setup_to_v2,
    plan_deployment,
    validate_deployment,
)
from endoreg_db.services.reference_data.catalog_export import export_reference_catalog

pytestmark = pytest.mark.django_db(transaction=True)


def test_deployment_contract_is_available_from_installed_distribution(
    tmp_path: Path,
) -> None:
    # Isolated Python ignores PYTHONPATH and the sibling checkout used by Pyright.
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            """
from importlib.resources import files
from lx_dtypes.models.contracts.deployment_setup import DeploymentSetup, DeploymentLock
from lx_dtypes.utils.deployment_setup import parse_deployment_setup, resolve_deployment
from lx_dtypes.terminology.terminology_service import TerminologyService
from pathlib import Path
setup = parse_deployment_setup(files('lx_dtypes').joinpath('setup_templates/minimal.yml').read_text())
assert isinstance(setup, DeploymentSetup)
resolved = resolve_deployment(setup, TerminologyService(Path('absent/registry.json').resolve()))
assert isinstance(resolved.lock, DeploymentLock)
""",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.fixture
def registry(tmp_path: Path) -> TerminologyService:
    service = TerminologyService(tmp_path / "terminology" / "registry.json")
    service.provision()
    return service


def template(name: str) -> DeploymentSetup:
    return parse_deployment_setup(
        files("lx_dtypes").joinpath("setup_templates", f"{name}.yml").read_text()
    )


def test_site_only_setup_needs_no_registry_or_study(tmp_path: Path) -> None:
    service = TerminologyService(tmp_path / "absent" / "registry.json")
    setup = template("minimal")
    resolved = validate_deployment(setup, service)
    plan = plan_deployment(resolved)
    assert plan.can_apply and plan.site_action == "create"
    assert not Center.objects.exists()
    assert not ApplicationSettings.objects.exists()
    assert not service.registry_path.exists()
    apply_deployment(setup, resolved.lock, service)
    row = Center.objects.get(center_key="local-center")
    assert ApplicationSettings.objects.get(pk=1).center == row
    assert apply_deployment(setup, resolved.lock, service).site_action == "reuse"
    assert Center.objects.count() == 1
    assert not service.registry_path.exists()


@pytest.mark.parametrize("name,count", [("training", 388), ("green_endoscopy", 502)])
def test_templates_apply_idempotently_with_closed_catalogues(
    registry: TerminologyService, name: str, count: int
) -> None:
    setup = template(name)
    before_registry = registry.registry_path.read_bytes()
    resolved = validate_deployment(setup, registry)
    assert plan_deployment(resolved).can_apply
    assert not ReferenceCatalogImport.objects.exists()
    assert registry.registry_path.read_bytes() == before_registry
    apply_deployment(setup, resolved.lock, registry)
    # Export also includes the explicitly configured local site center.
    assert len(export_reference_catalog().records) == count + 1
    assert all(
        p.unchanged
        for p in apply_deployment(setup, resolved.lock, registry).reference_plans
    )
    assert registry.registry_path.read_bytes() == before_registry


def test_missing_or_conflicting_package_prevents_site_creation(
    registry: TerminologyService,
) -> None:
    setup = template("training")
    Center.objects.create(center_key=setup.site.center_key, name="Different")
    resolved = validate_deployment(setup, registry)
    assert not plan_deployment(resolved).can_apply
    with pytest.raises(ValueError, match="conflicts"):
        apply_deployment(setup, resolved.lock, registry)
    assert not ReferenceCatalogImport.objects.exists()
    assert not ApplicationSettings.objects.exists()


def test_failure_in_later_package_rolls_back_everything(
    registry: TerminologyService,
) -> None:
    setup = template("training")
    resolved = validate_deployment(setup, registry)
    original = ReferenceCatalogImport.save

    def save(row: ReferenceCatalogImport, *_args: object, **_kwargs: object) -> None:
        if row.module == "endoreg_workforce":
            raise ValueError("injected later package failure")
        original(row)

    with patch.object(ReferenceCatalogImport, "save", save):
        with pytest.raises(ValueError, match="later package failure"):
            apply_deployment(setup, resolved.lock, registry)
    assert not ReferenceCatalogImport.objects.exists()
    assert not Center.objects.exists()
    assert not ApplicationSettings.objects.exists()


def test_dependency_edit_invalidates_reviewed_lock(
    registry: TerminologyService,
) -> None:
    setup = template("coloreg")
    resolved = validate_deployment(setup, registry)
    root = registry.source_paths("endoreg_reference", "2.0.0")[0]
    source = root / "terminology" / "endoreg_reference" / "catalogues.yml"
    source.write_text(source.read_text() + "\n# changed after plan\n")
    with pytest.raises(ValueError, match="sources differ"):
        apply_deployment(setup, resolved.lock, registry)
    assert not Center.objects.exists()


def test_manifest_edit_invalidates_reviewed_lock(registry: TerminologyService) -> None:
    setup = template("minimal")
    resolved = resolve_deployment(setup, registry)
    changed = setup.model_copy(update={"adopt_existing": True})
    with pytest.raises(ValueError, match="manifest differs"):
        apply_deployment(changed, resolved.lock, registry)
    assert not Center.objects.exists()


def test_activation_is_explicit_and_retry_preserves_database(
    registry: TerminologyService,
) -> None:
    setup = template("coloreg")
    setup.report_template_packages[0] = setup.report_template_packages[0].model_copy(
        update={"activate": True}
    )
    resolved = validate_deployment(setup, registry)
    previous = registry.active_identity()
    with patch.object(
        TerminologyService,
        "select",
        side_effect=TerminologyError(409, "registry changed"),
    ):
        with pytest.raises(DeploymentActivationError, match="Database setup committed"):
            apply_deployment(setup, resolved.lock, registry)
    assert ReferenceCatalogImport.objects.count() == 1
    assert registry.active_identity() == previous
    center = ApplicationSettings.objects.get(pk=1).center
    apply_deployment(setup, resolved.lock, registry)
    selected = setup.report_template_packages[0]
    assert registry.active_identity() == (selected.module, selected.version)
    assert ApplicationSettings.objects.get(pk=1).center == center
    assert ReferenceCatalogImport.objects.count() == 1


def test_concurrent_site_setup_uses_one_center(tmp_path: Path) -> None:
    service = TerminologyService(tmp_path / "unused.json")
    setup = template("minimal")
    lock = resolve_deployment(setup, service).lock

    def run(_index: int) -> None:
        try:
            apply_deployment(setup, lock, service)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(run, range(2)))
    assert Center.objects.filter(center_key="local-center").count() == 1
    assert ApplicationSettings.objects.count() == 1


def test_cli_plan_lock_and_apply(tmp_path: Path) -> None:
    source = tmp_path / "setup.yml"
    lock = tmp_path / "setup.lock.yml"
    source.write_text(
        files("lx_dtypes").joinpath("setup_templates", "minimal.yml").read_text()
    )
    service = TerminologyService(tmp_path / "unused.json")
    with patch(
        "endoreg_db.management.commands.setup_deployment.get_terminology_service",
        return_value=service,
    ):
        call_command(
            "setup_deployment", "validate", "--file", str(source), stdout=StringIO()
        )
        call_command(
            "setup_deployment",
            "plan",
            "--file",
            str(source),
            "--lock-file",
            str(lock),
            stdout=StringIO(),
        )
        assert not Center.objects.exists()
        assert (
            yaml.safe_load(lock.read_text())["setup"]["site"]["center_key"]
            == "local-center"
        )
        call_command(
            "setup_deployment",
            "apply",
            "--file",
            str(source),
            "--lock-file",
            str(lock),
            stdout=StringIO(),
        )
    assert ApplicationSettings.objects.get(pk=1).center is not None


def test_cli_plan_refuses_overwriting_manifest(tmp_path: Path) -> None:
    source = tmp_path / "setup.yml"
    content = files("lx_dtypes").joinpath("setup_templates", "minimal.yml").read_text()
    source.write_text(content)
    with pytest.raises(CommandError, match="must differ"):
        call_command(
            "setup_deployment",
            "plan",
            "--file",
            str(source),
            "--lock-file",
            str(source),
            stdout=StringIO(),
        )
    assert source.read_text() == content


def test_overlapping_catalogue_packages_reuse_shared_rows(
    registry: TerminologyService,
) -> None:
    from lx_dtypes.models.contracts.deployment_setup import DeploymentPackage

    setup = template("green_endoscopy")
    setup.reference_packages.insert(
        0, DeploymentPackage(module="endoreg_reference", version="2.0.0")
    )
    resolved = validate_deployment(setup, registry)
    assert plan_deployment(resolved).can_apply
    apply_deployment(setup, resolved.lock, registry)
    assert ReferenceCatalogImport.objects.count() == 2
    assert len(export_reference_catalog().records) == 503
    assert all(
        p.unchanged
        for p in apply_deployment(setup, resolved.lock, registry).reference_plans
    )


def test_existing_rows_require_explicit_adoption_in_setup(
    registry: TerminologyService,
) -> None:
    setup = template("training")
    resolved = validate_deployment(setup, registry)
    apply_deployment(setup, resolved.lock, registry)
    ReferenceCatalogImport.objects.all().delete()
    plan = plan_deployment(resolved)
    assert not plan.can_apply
    assert any(
        "explicit_legacy_adoption_required" in c.differing_fields
        for p in plan.reference_plans
        for c in p.changes
    )
    center = ApplicationSettings.objects.get(pk=1).center
    adopted = setup.model_copy(update={"adopt_existing": True})
    locked = validate_deployment(adopted, registry).lock
    apply_deployment(adopted, locked, registry)
    assert ApplicationSettings.objects.get(pk=1).center == center
    assert ReferenceCatalogImport.objects.count() == 2


def _local_reference_package(
    registry: TerminologyService,
    tmp_path: Path,
    name: str,
    *,
    package_kind: str | None,
) -> DeploymentSetup:
    """Register a local package and select it as the only reference package."""
    from lx_dtypes.models.contracts.deployment_setup import DeploymentPackage

    package_dir = tmp_path / "local_packages" / name
    package_dir.mkdir(parents=True)
    kind_line = f"package_kind: {package_kind}\n" if package_kind else ""
    # A reference catalog mixed with a terminology record has no migrated kind.
    (package_dir / "records.yml").write_text(
        "- model: reference_catalog\n"
        f"  name: {name}_shift_types\n"
        "  payload:\n"
        "    schema_version: '1.0'\n"
        "    records:\n"
        "    - record_type: shift_type\n"
        f"      name: {name}_shift\n"
        "      description: null\n"
        "      is_active: true\n"
        "- model: information_source\n"
        f"  name: {name}_source\n"
        "  description: Local test source.\n"
    )
    (package_dir / "config.yaml").write_text(
        f"name: {name}\nversion: 1.0.0\n{kind_line}"
        "modules: []\ndepends_on: []\ndata:\n  files:\n  - ./records.yml\n"
    )
    registry.register_local(name, "1.0.0", input_dirs=[package_dir.parent])
    setup = template("minimal")
    setup.reference_packages.append(DeploymentPackage(module=name, version="1.0.0"))
    return setup


V1_COLOREG = """schema_version: '1.0'
site:
  center_key: local-center
  name: Local Center
reference_packages:
- module: endoreg_reference
  version: 2.0.0
study_packages:
- module: coloreg
  version: 0.3.0
  activate: true
adopt_existing: false
"""


@pytest.mark.parametrize("name", ["minimal", "coloreg", "training", "green_endoscopy"])
def test_shipped_v2_templates_pass_the_role_kind_check(
    registry: TerminologyService, name: str
) -> None:
    setup = template(name)
    assert setup.schema_version == "2.0"
    validate_deployment(setup, registry)


def test_reference_package_with_catalog_kind_mismatch_fails_closed(
    registry: TerminologyService,
) -> None:
    from lx_dtypes.models.contracts.deployment_setup import DeploymentPackage

    setup = template("minimal")
    setup.reference_packages.append(
        DeploymentPackage(module="lx_findings", version="0.1.0")
    )
    with pytest.raises(
        DeploymentPackageKindError, match="expected application_data, found terminology"
    ):
        validate_deployment(setup, registry)
    lock = resolve_deployment(setup, registry).lock
    with pytest.raises(DeploymentPackageKindError, match="Pakettypen passen nicht"):
        apply_deployment(setup, lock, registry)
    assert not Center.objects.exists()
    assert not ReferenceCatalogImport.objects.exists()


def test_report_template_role_rejects_terminology_package(
    registry: TerminologyService,
) -> None:
    from lx_dtypes.models.contracts.deployment_setup import (
        DeploymentReportTemplatePackage,
    )

    setup = template("minimal")
    setup.report_template_packages.append(
        DeploymentReportTemplatePackage(module="lx_findings", version="0.1.0")
    )
    with pytest.raises(DeploymentPackageKindError, match="report_templates"):
        validate_deployment(setup, registry)


def test_reference_package_with_declared_kind_mismatch_fails_closed(
    registry: TerminologyService, tmp_path: Path
) -> None:
    setup = _local_reference_package(
        registry, tmp_path, "site_terminology_reference", package_kind="terminology"
    )
    with pytest.raises(DeploymentPackageKindError, match="found terminology"):
        validate_deployment(setup, registry)
    assert not Center.objects.exists()


def test_unclassified_reference_package_is_rejected_in_v2(
    registry: TerminologyService, tmp_path: Path
) -> None:
    setup = _local_reference_package(
        registry, tmp_path, "site_empty_reference", package_kind=None
    )
    with pytest.raises(DeploymentPackageKindError, match="unclassified"):
        validate_deployment(setup, registry)
    assert not Center.objects.exists()


def test_v1_migration_moves_study_selection_by_kind(
    registry: TerminologyService,
) -> None:
    from lx_dtypes.utils.deployment_setup import parse_any_deployment_setup
    from lx_dtypes.models.contracts.deployment_setup import DeploymentSetupV1

    original = parse_any_deployment_setup(V1_COLOREG)
    assert isinstance(original, DeploymentSetupV1)
    migrated = migrate_setup_to_v2(original, registry)
    assert migrated.schema_version == "2.0"
    assert [p.module for p in migrated.reference_packages] == ["endoreg_reference"]
    assert [(p.module, p.activate) for p in migrated.report_template_packages] == [
        ("coloreg", True)
    ]
    assert migrated.study_packages == []
    assert migrated.reference_packages == original.reference_packages
    validate_deployment(migrated, registry)


def test_v1_migration_rejects_activate_on_terminology(
    registry: TerminologyService,
) -> None:
    from lx_dtypes.utils.deployment_setup import parse_any_deployment_setup
    from lx_dtypes.models.contracts.deployment_setup import DeploymentSetupV1

    original = parse_any_deployment_setup(
        V1_COLOREG.replace(
            "module: coloreg\n  version: 0.3.0", "module: lx_findings\n  version: 0.1.0"
        )
    )
    assert isinstance(original, DeploymentSetupV1)
    with pytest.raises(DeploymentPackageKindError, match="activate is only allowed"):
        migrate_setup_to_v2(original, registry)


def test_cli_refuses_v1_and_migrates_to_a_new_file(
    registry: TerminologyService, tmp_path: Path
) -> None:
    source = tmp_path / "setup-v1.yml"
    source.write_text(V1_COLOREG)
    target = tmp_path / "setup-v2.yml"
    with patch(
        "endoreg_db.management.commands.setup_deployment.get_terminology_service",
        return_value=registry,
    ):
        for action in ("validate", "plan"):
            with pytest.raises(CommandError, match="setup_deployment migrate"):
                call_command(
                    "setup_deployment", action, "--file", str(source), stdout=StringIO()
                )
        with pytest.raises(CommandError, match="schema_version 1.0"):
            call_command(
                "setup_deployment",
                "apply",
                "--file",
                str(source),
                "--lock-file",
                str(tmp_path / "missing.lock.yml"),
                stdout=StringIO(),
            )
        with pytest.raises(CommandError, match="output must differ"):
            call_command(
                "setup_deployment",
                "migrate",
                str(source),
                "--output",
                str(source),
                stdout=StringIO(),
            )
        call_command(
            "setup_deployment",
            "migrate",
            str(source),
            "--output",
            str(target),
            stdout=StringIO(),
        )
        assert source.read_text() == V1_COLOREG
        migrated = yaml.safe_load(target.read_text())
        assert migrated["schema_version"] == "2.0"
        assert migrated["report_template_packages"][0]["module"] == "coloreg"
        with pytest.raises(CommandError, match="already exists"):
            call_command(
                "setup_deployment",
                "migrate",
                str(source),
                "--output",
                str(target),
                stdout=StringIO(),
            )
        call_command(
            "setup_deployment", "validate", "--file", str(target), stdout=StringIO()
        )


def test_coloreg_setup_uses_terminology_owned_references(
    registry: TerminologyService,
) -> None:
    from lx_dtypes.packages import load_registered_package
    from endoreg_db.services.reference_data.clinical_projection import (
        import_clinical_package,
    )

    setup = template("coloreg")
    assert setup.reference_packages[0].version == "2.0.0"
    resolved = validate_deployment(setup, registry)
    assert plan_deployment(resolved).can_apply
    first = apply_deployment(setup, resolved.lock, registry)
    assert first.reference_plans[0].clinical_plan is not None
    assert import_clinical_package(
        load_registered_package("coloreg", version="0.3.0")
    ).can_import
    assert all(
        plan.unchanged
        for plan in apply_deployment(setup, resolved.lock, registry).reference_plans
    )
