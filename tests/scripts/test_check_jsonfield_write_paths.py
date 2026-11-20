from __future__ import annotations

from pathlib import Path

from scripts.check_jsonfield_write_paths import (
    StandardizedJsonModel,
    discover_unvalidated_jsonfield_writes,
    standardized_json_models,
)
from scripts.check_lx_dtypes_model_inventory import DEFAULT_INVENTORY, load_inventory


def _models() -> dict[str, StandardizedJsonModel]:
    return {
        "Example": StandardizedJsonModel(
            label="endoreg_db.Example",
            fields=frozenset({"payload"}),
        )
    }


def _scan(tmp_path: Path, source: str) -> tuple[tuple[int, str, tuple[str, ...]], ...]:
    source_root = tmp_path / "endoreg_db"
    source_root.mkdir()
    (source_root / "writer.py").write_text(source, encoding="utf-8")

    issues = discover_unvalidated_jsonfield_writes(
        source_root=source_root,
        models=_models(),
        project_root=tmp_path,
    )

    return tuple((issue.line, issue.operation, issue.fields) for issue in issues)


def test_guard_rejects_direct_unvalidated_queryset_update(tmp_path: Path) -> None:
    issues = _scan(
        tmp_path,
        "from endoreg_db.models import Example\n"
        "Example.objects.filter(pk=1).update(payload={'version': 1})\n",
    )

    assert issues == ((2, "update", ("payload",)),)


def test_guard_accepts_recognizable_contract_validation(tmp_path: Path) -> None:
    issues = _scan(
        tmp_path,
        "from endoreg_db.models import Example\n"
        "canonical = Payload.model_validate(raw).model_dump(mode='json')\n"
        "Example.objects.filter(pk=1).update(payload=canonical)\n"
        "Example.objects.filter(pk=2).update(payload=normalize_payload(raw))\n",
    )

    assert issues == ()


def test_guard_resolves_static_double_star_mappings(tmp_path: Path) -> None:
    issues = _scan(
        tmp_path,
        "from endoreg_db.models import Example\n"
        "updates = {'status': 'ready'}\n"
        "updates.update({'payload': raw})\n"
        "Example.objects.filter(pk=1).update(**updates)\n",
    )

    assert issues == ((4, "update", ("payload",)),)


def test_guard_rejects_json_fields_named_in_bulk_update(tmp_path: Path) -> None:
    issues = _scan(
        tmp_path,
        "from endoreg_db.models import Example\n"
        "Example.objects.bulk_update(rows, ['status', 'payload'])\n",
    )

    assert issues == ((2, "bulk_update", ("payload",)),)


def test_guard_limits_bulk_create_to_explicit_json_values(tmp_path: Path) -> None:
    issues = _scan(
        tmp_path,
        "from endoreg_db.models import Example\n"
        "safe_default = Example(status='queued')\n"
        "unsafe = Example(payload=raw)\n"
        "Example.objects.bulk_create([safe_default])\n"
        "Example.objects.bulk_create([unsafe])\n",
    )

    assert issues == ((5, "bulk_create", ("payload",)),)


def test_guard_resolves_historical_migration_models(tmp_path: Path) -> None:
    issues = _scan(
        tmp_path,
        "def forwards(apps, schema_editor):\n"
        "    Example = apps.get_model('endoreg_db', 'Example')\n"
        "    Example.objects.all().update(payload=legacy_payload)\n",
    )

    assert issues == ((3, "update", ("payload",)),)


def test_guard_allows_reviewed_suppression_with_reason(tmp_path: Path) -> None:
    issues = _scan(
        tmp_path,
        "from endoreg_db.models import Example\n"
        "# jsonfield-write-guard: validated -- database function enforces v1\n"
        "Example.objects.filter(pk=1).update(payload=database_expression)\n",
    )

    assert issues == ()


def test_guard_ignores_non_json_updates_and_unresolved_receivers(
    tmp_path: Path,
) -> None:
    issues = _scan(
        tmp_path,
        "from endoreg_db.models import Example\n"
        "Example.objects.filter(pk=1).update(status='ready')\n"
        "queryset.update(payload=raw)\n",
    )

    assert issues == ()


def test_repository_has_no_detectable_unvalidated_jsonfield_bypasses() -> None:
    project_root = Path(__file__).resolve().parents[2]
    inventory = load_inventory(Path(DEFAULT_INVENTORY))

    issues = discover_unvalidated_jsonfield_writes(
        source_root=project_root / "endoreg_db",
        models=standardized_json_models(inventory),
        project_root=project_root,
    )

    assert issues == ()
