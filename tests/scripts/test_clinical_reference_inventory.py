from pathlib import Path

import pytest
import yaml

from scripts.check_clinical_reference_inventory import (
    ClinicalInventory,
    validate_inventory,
)


def test_reviewed_inventory_covers_all_graph_fields_and_bootstrap_commands() -> None:
    root = Path(__file__).resolve().parents[2]
    inventory = ClinicalInventory.model_validate(
        yaml.safe_load((root / "quality/clinical_reference_projection.yml").read_text())
    )
    source = (root / "endoreg_db/management/commands/load_base_db_data.py").read_text()
    validate_inventory(inventory, source)
    inventory.collections["finding"].fields.remove("interventions")
    with pytest.raises(ValueError, match="field inventory"):
        validate_inventory(inventory, source)


def test_unclassified_bootstrap_command_fails_inventory() -> None:
    root = Path(__file__).resolve().parents[2]
    inventory = ClinicalInventory.model_validate(
        yaml.safe_load((root / "quality/clinical_reference_projection.yml").read_text())
    )
    source = (root / "endoreg_db/management/commands/load_base_db_data.py").read_text()
    with pytest.raises(ValueError, match="ownership inventory"):
        validate_inventory(inventory, source + '\ncall_command("new_loader")\n')
