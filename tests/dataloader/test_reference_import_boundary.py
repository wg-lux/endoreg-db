"""The retired arbitrary YAML-to-ORM API has no supported entry point."""

from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from scripts.check_clinical_reference_inventory import validate_legacy_retirement


def test_host_has_no_legacy_engine_or_production_imports() -> None:
    validate_legacy_retirement(Path(__file__).resolve().parents[2])


@pytest.mark.parametrize("option", ["source_directory", "yaml_directory", "model"])
def test_import_command_has_no_arbitrary_filesystem_or_model_option(
    option: str,
) -> None:
    with pytest.raises(TypeError, match="Unknown option"):
        call_command(
            "import_reference_catalog",
            module="example",
            module_version="1.0.0",
            **{option: "/unregistered/source"},
        )


def test_registry_identity_mismatch_never_reaches_projection() -> None:
    with (
        patch(
            "endoreg_db.management.commands.import_reference_catalog.get_terminology_service"
        ) as service,
        patch(
            "endoreg_db.management.commands.import_reference_catalog.catalog_snapshot"
        ) as project,
    ):
        service.return_value.load.return_value.config.name = "different_package"
        service.return_value.load.return_value.config.version = "1.0.0"
        with pytest.raises(CommandError, match="requested identity"):
            call_command(
                "import_reference_catalog",
                module="requested",
                module_version="1.0.0",
                stdout=StringIO(),
            )
    project.assert_not_called()
